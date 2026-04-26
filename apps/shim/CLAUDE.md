# apps/shim — Token-Check Shim

**Freshness:** 2026-04-26

## Purpose

Starlette + httpx reverse proxy that sits between App Gateway and researcher ACA apps. Its single job per request: decide whether this request is allowed to reach the upstream, and if so, proxy it and record an access log entry. It is the only component outside the control plane that handles reviewer tokens.

## Request flow (`src/rac_shim/main.py::_handle`)

1. Resolve app slug from `Host` header via `AppRegistry` (Postgres-backed cache refreshed every N seconds).
2. If `access_mode == "public"` → proxy directly, log, return. Also handles cold-start interstitial if upstream 502s.
3. If `access_mode == "token_required"`:
   - `rac_token` query param present → first-use path: validate JWS signature with KV public key, check claims (`iss`, `aud=rac-app:<slug>`, `exp`), check `revoked_token` denylist by `jti`, build cookie `rac_session` (HMAC-signed), 302 redirect to the same URL with `rac_token` stripped.
   - Otherwise parse the `rac_session` cookie, re-check denylist, proxy if valid.
   - No token, no valid cookie → render error HTML.
4. Cold-start detection: upstream 502 OR latency > `cold_start_threshold_ms` triggers `render_interstitial` and fires a background wake task. The interstitial polls `/_rac/wake` which is always 204 — the JS just uses it as a liveness ping.
5. Every branch (including errors) writes an `AccessRecord` to `AccessLogBatchWriter`.

## Package layout

- `token/` — **Pure** JWT/JWS validation, cookie build+parse, KV public-key cache, denylist cache. `validation.py` is fully pure; `kv_key_cache.py` and `denylist_cache.py` are the shell.
- `routing/decision.py` — Pure `route_for_host(host, parent_domain, routes) -> AppRoute | None`.
- `proxy/forward.py` — `proxy_request()` wraps an httpx call, preserves streaming where possible, strips hop-by-hop headers.
- `proxy/wake_up.py` — Fire-and-record wake helper (HEAD with long timeout).
- `cold_start/decision.py` — Pure `decide(upstream_status, latency_ms, threshold)` → `Decision(should_serve_interstitial: bool)`.
- `audit/` — `AccessRecord` dataclass (pure build), `AccessLogBatchWriter` (asyncpg COPY batch, own asyncio task).
- `ui/templates/` + `ui/render.py` — Branded HTML for interstitial + 4 error pages. Templates are strings; renderer is pure.
- `app_registry.py` — Reads `app` + `app_version` from Postgres as `rac_shim`; refreshes periodically.

## Invariants

- **Read-only + one insert.** The shim DB role (`rac_shim`) can only SELECT `app`, `app_version`, `revoked_token`, and INSERT `access_log`. If you find yourself adding a query that needs another grant, stop — either push that logic into the control plane, or re-design. Never expand the grants.
- **KV key cache TTL.** Public keys are cached in-process with TTL; a cache miss goes to KV. The key name format is `rac-app-{slug}-v1` and is stable. On key rotation the old key stays valid until the TTL expires; the denylist absorbs compromised tokens.
- **Denylist cache is authoritative-eventually.** Local cache hits a short TTL; misses hit Postgres. The cache is **best-effort**: on a race, a freshly-revoked token may be accepted for up to TTL seconds. This is by design; for instant revocation, issue a new signing key and rotate.
- **No state mutation on the request path beyond the batch writer.** All side effects are either read-through caches or the async access-log append. The writer owns its own task and catches exceptions in `_run` (regression fixed in Phase 6 — don't re-introduce).
- **`_Deps` is the single injection seam.** Tests build a `_Deps` and call `create_app(deps=d)`. Production lifespan builds it from settings.

## Metrics (AC10.2)

`token_validation_counter{result=valid|expired|revoked|malformed}`, `wake_up_duration_histogram`. Exported via OTLP when `metrics_enabled`.

## Deployment

Deployed as a single ACA container app via `infra/modules/shim-aca-app.bicep`. The App Gateway backend pool points at the shim's internal FQDN. All researcher apps are internal-only ACA apps; only the shim is reachable from outside.

**Runtime quirks worth remembering:**
- `aiohttp>=3.10` is a hard dep (declared in `pyproject.toml`) because `azure-identity`'s async credential flow imports it. Removing it breaks shim startup.
- The Dockerfile invokes gunicorn as `python -m gunicorn …`, not as a bare `gunicorn` command. uv produces venv shebangs with absolute paths to the build-stage `/app/.venv`; those paths don't survive `COPY --from=builder` into the runtime stage and cause `exec format error`. Don't change the entrypoint back to `gunicorn …`.

## Tests

`tests/` uses a test `_Deps` with a fake httpx transport + a Postgres test container. Key flows: public-proxy, token-first-use, cookie-reuse, denylist-hit, cold-start-interstitial, host-not-found, malformed-token.
