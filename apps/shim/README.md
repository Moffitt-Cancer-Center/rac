# rac-shim

The shim is the **single public entry point** for all researcher applications deployed in RAC.

## Role

Every HTTP request destined for `<slug>.<PARENT_DOMAIN>` passes through the shim before reaching
the researcher app. The shim:

1. **Resolves the target app** from the incoming `Host` header by extracting the slug and looking it
   up in its in-memory `AppRoute` table (refreshed every 30 s from Postgres).

2. **Validates reviewer tokens** (first-use path): if a `rac_token` query parameter is present,
   the shim fetches the per-app public key from Azure Key Vault (5-min TTL cache), verifies the
   ES256 JWT signature, checks issuer/audience/expiry/`nbf`, confirms the `jti` is not in the
   revocation denylist (60-s TTL from Postgres), then sets an HttpOnly/Secure/SameSite=Lax
   `rac_session` HMAC-signed cookie and redirects to the clean URL.

3. **Checks session cookies** (subsequent requests): extracts the `jti` from the `rac_session`
   cookie, verifies the HMAC and expiry, and checks the denylist.

4. **Handles `access_mode=public` apps** without any token validation; still writes an `access_log`
   row with `token_jti=NULL`.

5. **Serves cold-start interstitials** when the upstream app returns 503/504 or does not respond,
   and dispatches a background wake request.

6. **Proxies** all authenticated requests to the internal upstream via `httpx` streaming.

7. **Writes an append-only `access_log` row** for every proxied request using asyncpg COPY batching
   (flush every 2 s or 5000 records).

8. **Emits structured JSON logs** (structlog) and OpenTelemetry metrics
   (`rac.shim.token_validations`, `rac.shim.wake_up_duration_ms`).

## Architecture

FCIS strict. Pure modules live under `token/`, `routing/`, `cold_start/`. Shell modules live under
`proxy/`, `audit/`, and `main.py`.

## Running locally

```bash
uv run --project apps/shim uvicorn rac_shim.main:app --reload --port 8080
```

## Tests

```bash
uv run --project apps/shim pytest tests/ -v
uv run --project apps/shim mypy src
uv run --project apps/shim ruff check src
```
