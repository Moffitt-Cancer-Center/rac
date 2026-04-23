# Phase 6: Token-Check Shim

**Goal:** A reverse proxy service at `apps/shim/` that is the single public entry point to all researcher apps. It validates JWT reviewer tokens (signature via per-app Key Vault public keys, expiry, audience, issuer, denylist with 60s TTL), sets HttpOnly/Secure/SameSite=Lax cookies after first successful use, serves branded cold-start interstitial and error pages, writes an append-only `access_log` row per request (async batched), and handles `access_mode=public` apps without token validation.

**Language decision:** **Python 3.12 with Starlette + httpx.** Rationale:
- Design allows "Go or Python" — either works.
- Keeps one language for the whole backend stack → shared schemas (reviewer_token payload shape, access_log record), shared test harnesses (Postgres testcontainer, mock OIDC).
- joserfc (from Authlib) is a modern, well-maintained JWT library; ES256 validation performance is adequate at v1 scale (10–30 apps).
- `httpx.AsyncClient` + Starlette Streaming gives a clean reverse-proxy boundary.
- If v1 load tests reveal hot-path latency issues on JWT validation or async batched writes, a Go rewrite of the shim is a self-contained replacement; the contract is the same.

**Architecture:** FCIS strict. Pure modules: JWT claim validation, audience/issuer/expiry checks, denylist membership check, cookie-string builders, cold-start decision logic, access_log record construction. Shell modules: Key Vault public key fetcher + 5-min TTL cache, Postgres `revoked_token` denylist fetcher + 60s TTL cache, `httpx` upstream proxy, async batched `access_log` writer (asyncpg + `copy_records_to_table`, flush every 2s or at 5000 records), embedded HTML templates for interstitial + error pages.

**Tech Stack:** Python 3.12, Starlette 0.37+, `httpx` (AsyncClient, streaming), `joserfc>=1.1` (JWT), `asyncpg` (for batched `access_log` writes), `azure-keyvault-keys` (public key fetch), `azure-identity` (managed identity), `structlog`, `prometheus-client` (optional — feature-flagged). Deployed as an ACA app with `min-replicas=1` (the shim itself cannot scale to zero; it's the entry point).

**Scope:** Phase 6 of 8.

**Codebase verified:** 2026-04-23 — `apps/shim/` does not exist. Phase 5 delivered per-app signing keys in Key Vault and per-app ACA apps reachable at `<slug>.internal.<env>.azurecontainerapps.io`. `reviewer_token` and `revoked_token` tables exist from Phase 2 migration. Token issuance by Control Plane is Phase 7 — until Phase 7 ships, this phase tests against synthesized JWTs produced by a test fixture using the same Key Vault key path.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC6: Approved apps deploy automatically (cold-start interstitial portion)
- **rac-v1.AC6.2 Success:** The app resolves at `https://<slug>.${PARENT_DOMAIN}`; when no replicas are warm, the shim serves the "waking up" interstitial, wakes the app, and redirects to the original URL within the configured wake budget.

### rac-v1.AC7: Reviewer access works via tokenized URLs
- **rac-v1.AC7.1 Success:** A valid token presented via URL query sets an HttpOnly/Secure/SameSite=Lax cookie; shim redirects to the clean URL (token stripped); the access is written to `access_log`.
- **rac-v1.AC7.2 Success:** A revoked token (`jti` present in `revoked_token`) is rejected within 60 seconds of revocation and the user sees the branded "revoked" page.
- **rac-v1.AC7.3 Success:** An expired token produces the branded "expired" page with the researcher's contact email and PI name.
- **rac-v1.AC7.4 Failure:** A malformed, forged, or wrong-audience token returns a generic 403 page that does not disclose the specific validation failure.
- **rac-v1.AC7.5 Edge:** An app in `access_mode=public` serves all requests without token validation; `access_log` rows are still written with `token_jti=NULL`.
- **rac-v1.AC7.6 Edge:** A valid token issued for App A is rejected when presented at App B (audience-claim mismatch).

### rac-v1.AC10: Observability (shim portion)
- **rac-v1.AC10.1 Success:** The shim writes an `access_log` row for every proxied request, including requests served under `access_mode=public`.
- **rac-v1.AC10.4 Success (shim portion):** Shim logs are structured JSON with stable fields (`submission_id`, `app_id`, `principal_id`, `request_id`) in Log Analytics.

### rac-v1.AC12: Cross-cutting audit hygiene
- **rac-v1.AC12.1 (shim portion):** `access_log` writes are append-only; no `UPDATE`/`DELETE` paths exist in the shim.
- **rac-v1.AC12.2 (shim portion):** Every Shim error response body includes a `request_id` (== the correlation ID) that is also present in the corresponding Log Analytics structured-log line for that request.
- **rac-v1.AC12.3 (shim portion):** Shim error responses never leak Postgres error text, stack traces, internal URIs, or token validation detail to the client. Error pages show only a user-facing message and a `request_id` for support purposes.

### rac-v1.AC10: Observability — shim custom metrics
- **rac-v1.AC10.2 Success (shim portion):** Custom metrics `rac.shim.token_validations` (counter, labeled by `result`: `valid`/`expired`/`revoked`/`malformed`) and `rac.shim.wake_up_duration_ms` (histogram) are emitted via OpenTelemetry on each token validation and each cold-start wake cycle.

**Verifies:** Functionality phase. Each task names which AC cases it tests.

---

## File Classification Policy

- `apps/shim/src/rac_shim/token/validation.py`, `audience.py`, `denylist_check.py`, `cookie.py`, `routing_decision.py`, `cold_start.py`, `access_record.py`: Functional Core.
- `apps/shim/src/rac_shim/token/kv_key_cache.py`, `denylist_cache.py`: Imperative Shell (network/DB + cache).
- `apps/shim/src/rac_shim/proxy/forward.py`: Imperative Shell.
- `apps/shim/src/rac_shim/audit/batch_writer.py`: Imperative Shell.
- `apps/shim/src/rac_shim/main.py`: Imperative Shell.
- Embedded HTML templates (`.html` files): exempt.
- Tests: exempt.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Shim package scaffold and deps

**Verifies:** None (setup)

**Files:**
- Modify: `/home/sysop/rac/pyproject.toml` (add `apps/shim` to uv workspace members)
- Create: `/home/sysop/rac/apps/shim/pyproject.toml`
- Create: `/home/sysop/rac/apps/shim/src/rac_shim/__init__.py`
- Create: `/home/sysop/rac/apps/shim/tests/__init__.py`
- Create: `/home/sysop/rac/apps/shim/conftest.py`
- Create: `/home/sysop/rac/apps/shim/Dockerfile`
- Create: `/home/sysop/rac/apps/shim/README.md`

**Implementation:**

`pyproject.toml` deps: `starlette>=0.37`, `uvicorn[standard]`, `gunicorn`, `httpx`, `joserfc>=1.1`, `asyncpg`, `azure-identity`, `azure-keyvault-keys`, `azure-keyvault-secrets`, `structlog`, `pydantic-settings`. Dev: `pytest`, `pytest-asyncio`, `hypothesis`, `testcontainers[postgres]`, `respx`, `freezegun`, `ruff`, `mypy`.

Dockerfile: multi-stage (`uv sync` venv stage → `python:3.12-slim` runtime). Non-root user. Healthcheck at `/_shim/health`. CMD uvicorn-workers.

**Verification:**
```bash
cd /home/sysop/rac && uv sync
uv run --project apps/shim pytest --collect-only
```

**Commit:** `feat(shim): package scaffold`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Pure token validation (Functional Core)

**Verifies:** `rac-v1.AC7.1`, `rac-v1.AC7.3`, `rac-v1.AC7.4`, `rac-v1.AC7.6`

**Files:**
- Create: `apps/shim/src/rac_shim/token/__init__.py`
- Create: `apps/shim/src/rac_shim/token/claims.py` (type-only)
- Create: `apps/shim/src/rac_shim/token/validation.py` (pattern: Functional Core)
- Create: `apps/shim/src/rac_shim/token/audience.py` (pattern: Functional Core)
- Create: `apps/shim/src/rac_shim/token/denylist_check.py` (pattern: Functional Core)
- Create: `apps/shim/src/rac_shim/token/errors.py` (pattern: Functional Core — error taxonomy)
- Create: `apps/shim/tests/test_token_validation.py`
- Create: `apps/shim/tests/test_audience.py`

**Implementation:**

`claims.py`:
```python
@dataclass(frozen=True)
class RacTokenClaims:
    iss: str
    aud: str                 # Expected format: "rac-app:{slug}"
    sub: str                 # Reviewer label or principal ID
    jti: UUID
    iat: datetime
    exp: datetime
    nbf: datetime | None
    scope: str | None        # "read" default
```

`errors.py`: `class TokenInvalid(Exception)` with subclasses `Expired`, `WrongAudience`, `WrongIssuer`, `SignatureInvalid`, `Malformed`, `Revoked`, `NotYetValid`. Each has a `code: str` and `internal_detail: str`. The shim's view rendering NEVER exposes `internal_detail` to the user (AC7.4) — it uses only `code` to pick the branded page.

`validation.py`:
- `def decode_unverified_header(token: str) -> dict`: parses the header without verifying signature, returns `kid`. Wraps `joserfc.jwt.decode_with_header`.
- `def verify_signature_and_claims(token: str, *, public_key: JsonWebKey, expected_issuer: str, expected_audience: str, now: datetime) -> RacTokenClaims`:
  - Uses `joserfc.jwt.decode(token, key=public_key, algorithms=['ES256'])`.
  - Enforces `iss == expected_issuer` (AC7.4).
  - Enforces `aud == expected_audience` (`"rac-app:{slug}"` — AC7.6).
  - Enforces `exp > now` (AC7.3).
  - Enforces `nbf <= now` if present.
  - On any violation, raises the appropriate `TokenInvalid` subclass.
  - Returns `RacTokenClaims` on success.

`audience.py`:
- `def expected_audience_for_host(host: str, parent_domain: str) -> str | None`: parses `<slug>.<parent_domain>` → returns `f"rac-app:{slug}"`; None if host doesn't match.

`denylist_check.py`:
- `def is_revoked(jti: UUID, denylist: frozenset[UUID]) -> bool`: pure set membership.

Tests (heavy use of Hypothesis + freezegun):
- `test_token_validation.py`:
  - Well-formed valid token → claims returned.
  - Expired token → `Expired` raised (AC7.3).
  - Wrong issuer → `WrongIssuer`.
  - Wrong audience (token minted for App A, checked against App B) → `WrongAudience` (AC7.6).
  - Malformed (truncated, wrong signature format) → `Malformed`.
  - Signature forged (correct format but wrong key) → `SignatureInvalid`.
  - `nbf > now` → `NotYetValid`.
  - Property test: for any `(now, exp)` with `exp > now`, validation succeeds (keeping other claims valid); for any `(now, exp)` with `exp <= now`, validation raises `Expired`.
- `test_audience.py`:
  - `"foo.rac.example.com" + "rac.example.com"` → `"rac-app:foo"`.
  - Non-matching host → None.
  - Trailing dot, case differences, port numbers — edge cases covered.

Property-based testing skill applies (roundtrip encode/decode, monotonicity of expiry).

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_token_validation.py apps/shim/tests/test_audience.py -v
```

**Commit:** `feat(shim): pure JWT validation + audience matching`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Cookie + routing decision + cold-start decision (Functional Core)

**Verifies:** `rac-v1.AC7.1`, `rac-v1.AC6.2`

**Files:**
- Create: `apps/shim/src/rac_shim/token/cookie.py` (pattern: Functional Core)
- Create: `apps/shim/src/rac_shim/routing/__init__.py`
- Create: `apps/shim/src/rac_shim/routing/decision.py` (pattern: Functional Core)
- Create: `apps/shim/src/rac_shim/cold_start/__init__.py`
- Create: `apps/shim/src/rac_shim/cold_start/decision.py` (pattern: Functional Core)
- Create: `apps/shim/tests/test_cookie.py`
- Create: `apps/shim/tests/test_routing_decision.py`
- Create: `apps/shim/tests/test_cold_start_decision.py`

**Implementation:**

`cookie.py` (pure):
- `def build_cookie_header(claims: RacTokenClaims, max_age_seconds: int, cookie_domain: str) -> str`: returns `"rac_session=<signed-payload>; Path=/; Domain=<domain>; Max-Age=<n>; HttpOnly; Secure; SameSite=Lax"`. The signed-payload is a short-lived JWT containing `jti`, `exp`, `app_slug` — signed with a shim-internal HMAC secret (distinct from per-app signing keys) so cookies can be verified on subsequent requests without re-reading the original reviewer JWT.
- `def extract_session_jti(cookie_header: str | None, hmac_secret: bytes) -> UUID | None`: parses and verifies; returns None on invalid or missing. Pure.

`routing/decision.py` (pure):
- `@dataclass(frozen=True) class AppRoute: slug: str, upstream_host: str, access_mode: Literal['token_required','public']`. Built from `app` table rows.
- `def route_for_host(host: str, routes: Mapping[str, AppRoute]) -> AppRoute | None`: lookup by `slug` extracted from host; returns None on unknown slug.

`cold_start/decision.py` (pure):
- `@dataclass(frozen=True) class ColdStartDecision: should_serve_interstitial: bool, wake_call_target: str | None`.
- `def decide(upstream_response_code: int | None, upstream_latency_ms: float | None, cold_start_threshold_ms: int) -> ColdStartDecision`: if upstream 503/504 OR latency > threshold OR response is a container-not-ready marker → serve interstitial with a wake-up fetch target. Pure.

Tests: property-based + examples covering each branch, including SameSite/Secure attributes in the generated cookie (AC7.1).

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_cookie.py apps/shim/tests/test_routing_decision.py apps/shim/tests/test_cold_start_decision.py -v
```

**Commit:** `feat(shim): pure cookie, routing, cold-start decision`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-6) -->

<!-- START_TASK_4 -->
### Task 4: Key Vault public-key cache + denylist cache (Shell)

**Verifies:** `rac-v1.AC7.1`, `rac-v1.AC7.2`

**Files:**
- Create: `apps/shim/src/rac_shim/token/kv_key_cache.py` (pattern: Imperative Shell)
- Create: `apps/shim/src/rac_shim/token/denylist_cache.py` (pattern: Imperative Shell)
- Create: `apps/shim/tests/test_kv_key_cache.py`
- Create: `apps/shim/tests/test_denylist_cache.py`

**Implementation:**

`kv_key_cache.py`:
- `class KeyVaultPublicKeyCache`:
  - `__init__(self, kv_uri, credential, ttl_seconds=300)`
  - `async def get_jwk(self, key_name: str) -> JsonWebKey`: checks in-memory cache; on miss, fetches public key from Key Vault via `KeyClient.get_key(key_name).key` (returns `JsonWebKey`), converts to joserfc JWK, caches with TTL. Thread-safe via `asyncio.Lock` per key_name.
  - Public-only: never requests private key material.

`denylist_cache.py`:
- `class RevokedTokenDenylistCache`:
  - `__init__(self, pg_pool, ttl_seconds=60)` — matches design's 60s revocation window (AC7.2).
  - `async def current_denylist() -> frozenset[UUID]`: returns cached set if fresh; otherwise `SELECT jti FROM revoked_token WHERE (expires_at IS NULL OR expires_at > NOW())`, updates cache.
  - `async def check(jti: UUID) -> bool`: calls `current_denylist()` then `jti in denylist`.

Tests:
- `test_kv_key_cache.py` (mock `KeyClient` with `unittest.mock`): cache miss fetches once; subsequent calls within TTL don't re-fetch; after TTL, re-fetches. Property-ish: never fetches private key methods.
- `test_denylist_cache.py` (testcontainers Postgres): insert a revoked jti → `check` returns True within TTL; expire TTL via freezegun → re-queries; delete row → `check` returns False after next refresh. AC7.2: "within 60 seconds" — assert that a jti inserted at t=0 is visible at t=60 (post-TTL refresh) and may or may not be visible at t=59 (cache may still be stale; that's the design trade-off).

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_kv_key_cache.py apps/shim/tests/test_denylist_cache.py -v
```

**Commit:** `feat(shim): public-key cache + denylist cache`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Access log batched writer (Shell)

**Verifies:** `rac-v1.AC10.1`, `rac-v1.AC7.5`, `rac-v1.AC12.1`

**Files:**
- Create: `apps/shim/src/rac_shim/audit/__init__.py`
- Create: `apps/shim/src/rac_shim/audit/access_record.py` (pattern: Functional Core)
- Create: `apps/shim/src/rac_shim/audit/batch_writer.py` (pattern: Imperative Shell)
- Create: `apps/shim/tests/test_access_record.py`
- Create: `apps/shim/tests/test_batch_writer.py`

**Implementation:**

`access_record.py` (pure):
- `@dataclass(frozen=True) class AccessRecord`: `id: UUID`, `app_id: UUID`, `submission_id: UUID | None`, `reviewer_token_jti: UUID | None`, `access_mode: Literal['token_required','public']`, `host: str`, `path: str`, `method: str`, `upstream_status: int | None`, `latency_ms: int`, `user_agent: str | None`, `source_ip: str`, `created_at: datetime`, `request_id: UUID`.
- `def build_record(request_info: RequestInfo, route: AppRoute, token_jti: UUID | None, upstream_status: int | None, latency_ms: int) -> AccessRecord`: constructs the row. Pure.

`batch_writer.py` (shell):
- `class AccessLogBatchWriter`:
  - `__init__(pg_pool, batch_size=5000, flush_interval_seconds=2.0, max_queue_size=50000)`
  - `async def append(record: AccessRecord)`: non-blocking; enqueues; if queue full → drops record and logs a WARN with metric (backpressure signal).
  - Background task loop: flush every `flush_interval_seconds` OR when queue ≥ `batch_size`. Flush uses `asyncpg.Connection.copy_records_to_table('access_log', records=..., columns=...)` for COPY performance.
  - Graceful shutdown: drain queue on SIGTERM.

`tests/test_access_record.py`:
- Pure: token-required path → `reviewer_token_jti=<jti>`, `access_mode='token_required'`.
- Public path → `reviewer_token_jti=None` (AC7.5).
- Property test: every record includes a valid UUID id, non-negative latency, a non-empty path.

`tests/test_batch_writer.py` (testcontainers Postgres):
- Append N records ≥ batch_size → flushed to DB in one call.
- Append a few records, wait `flush_interval_seconds` → flushed.
- Append after queue full → dropped record count incremented; previous records still flush.
- AC12.1: attempting to UPDATE or DELETE `access_log` via the `rac_shim` DB role fails with permission denied. **Decision (pinned in README.md):** the Shim uses the `rac_shim` Postgres role, which is separate from the Control Plane's `rac_app` role. `rac_shim` is created in Alembic migration `0005_rac_shim_db_role.py` (Phase 6 Task 9). This test must run with a fixture that connects as `rac_shim`; the testcontainers Postgres fixture creates both roles as part of migration setup.

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_access_record.py apps/shim/tests/test_batch_writer.py -v
```

**Commit:** `feat(shim): async batched access_log writer`
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Upstream proxy (httpx + Starlette streaming)

**Verifies:** `rac-v1.AC6.2`, `rac-v1.AC7.1`, `rac-v1.AC7.5`

**Files:**
- Create: `apps/shim/src/rac_shim/proxy/__init__.py`
- Create: `apps/shim/src/rac_shim/proxy/forward.py` (pattern: Imperative Shell)
- Create: `apps/shim/src/rac_shim/proxy/wake_up.py` (pattern: Imperative Shell)
- Create: `apps/shim/tests/test_proxy_forward.py`

**Implementation:**

`proxy/forward.py`:
- `async def proxy_request(request: Request, upstream_host: str, upstream_path: str, client: httpx.AsyncClient, timeout_seconds: float = 30) -> StarletteResponse`:
  - Build upstream URL `http://{upstream_host}{upstream_path}?{query}`.
  - Strip `Authorization` + `Cookie` headers before forwarding (the shim's cookie is only for the shim's own token-check; upstream gets a clean request with `X-RAC-Reviewer-Label`, `X-RAC-Reviewer-Jti`, `X-RAC-App-Slug` headers).
  - Forward method + body via `client.stream(...)`; return `StreamingResponse` wrapping the upstream response.
  - Preserve headers except hop-by-hop (`Connection`, `Keep-Alive`, `Proxy-Authenticate`, `Proxy-Authorization`, `TE`, `Trailers`, `Transfer-Encoding`, `Upgrade`).
  - Timeout or 5xx → return None-ish sentinel so caller can decide cold-start path.

`proxy/wake_up.py`:
- `async def wake(upstream_host: str, client: httpx.AsyncClient) -> None`: fires an HTTP GET to `http://{upstream_host}/_rac/wake` (or `/` if no wake endpoint). Purpose: nudge ACA scale-from-zero without the reviewer waiting. Returns when upstream replies or timeout (max 20s).

`tests/test_proxy_forward.py`:
- Mock upstream via `respx` or a local aiohttp server; verify headers stripped/added correctly (AC7.1 — `Authorization` NOT forwarded; `X-RAC-Reviewer-Label` added).
- Streaming body preserved: send a 10 MB body in chunks; assert client sees bytes in chunks.
- Public mode path: same as token-required path but `X-RAC-Reviewer-*` headers are absent (AC7.5).

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_proxy_forward.py -v
```

**Commit:** `feat(shim): httpx streaming upstream proxy + wake helper`
<!-- END_TASK_6 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 7-9) -->

<!-- START_TASK_7 -->
### Task 7: Branded error + interstitial HTML templates

**Verifies:** `rac-v1.AC7.2`, `rac-v1.AC7.3`, `rac-v1.AC7.4`, `rac-v1.AC6.2`

**Files:**
- Create: `apps/shim/src/rac_shim/ui/__init__.py`
- Create: `apps/shim/src/rac_shim/ui/templates/base.html`
- Create: `apps/shim/src/rac_shim/ui/templates/interstitial.html`
- Create: `apps/shim/src/rac_shim/ui/templates/error_expired.html`
- Create: `apps/shim/src/rac_shim/ui/templates/error_revoked.html`
- Create: `apps/shim/src/rac_shim/ui/templates/error_generic.html`
- Create: `apps/shim/src/rac_shim/ui/render.py` (pattern: Functional Core — pure template rendering with string.Template)
- Create: `apps/shim/tests/test_ui_render.py`

**Implementation:**

Plain HTML (no Jinja) with `string.Template` substitution for institution_name, brand_logo_url, researcher_contact_email, pi_name, request_id. This keeps rendering pure and dependency-light.

Templates:
- `interstitial.html`: shows a waking-up spinner + institution branding, JS that fires a wake call to `/_rac/wake` and then reloads after response. Includes a "🌐 Public" banner when `access_mode=public` (per design).
- `error_expired.html` (AC7.3): "This reviewer link has expired" + researcher_contact_email + pi_name.
- `error_revoked.html` (AC7.2): "This reviewer access has been revoked" + generic contact block.
- `error_generic.html` (AC7.4): "Access denied" + correlation_id — NO validation-specific details. The same template covers Malformed, SignatureInvalid, WrongIssuer, WrongAudience, NotYetValid — all return the same user-facing 403 page. Internal logs capture the specific `code` and `internal_detail`.

`render.py` (pure):
- `def render_error(code: ErrorCode, context: ErrorContext) -> bytes`: picks the template based on `code`, substitutes context fields. `ErrorContext` includes `institution_name`, `brand_logo_url`, `researcher_contact_email`, `pi_name`, `correlation_id`. Pure.
- `def render_interstitial(context: InterstitialContext) -> bytes`: similar.

Tests: render each template with fixture context; assert user-facing HTML contains the expected strings (AC7.3 contact/PI) and does NOT contain the underlying error type (AC7.4).

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_ui_render.py -v
```

**Commit:** `feat(shim): branded error + interstitial HTML`
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Shim app wiring (main.py + lifespan)

**Verifies:** All AC7.* and AC6.2 integration

**Files:**
- Create: `apps/shim/src/rac_shim/main.py` (pattern: Imperative Shell)
- Create: `apps/shim/src/rac_shim/settings.py` (pattern: Imperative Shell)
- Create: `apps/shim/src/rac_shim/logging_setup.py` (pattern: Imperative Shell)
- Create: `apps/shim/src/rac_shim/app_registry.py` (pattern: Imperative Shell — loads `app` rows from Postgres, caches for routing)
- Create: `apps/shim/tests/test_main_flow.py` (integration)

**Implementation:**

`settings.py`: Pydantic-settings with `RAC_SHIM_` prefix. Fields: database DSN, Key Vault URI, parent_domain, issuer, cookie_hmac_secret (SecretStr, fetched from Key Vault at startup), wake_upstream_timeout_seconds, cold_start_threshold_ms, batch writer config, institution branding, researcher_contact_email template (e.g., `"{pi_name}@{institution_domain}"`), **`wake_budget_seconds: int = 20`** (default 20s — the maximum wall-clock time from first interstitial response to upstream 200, as measured by the acceptance test in Task 10. Pinned in README.md cross-phase decisions).

`app_registry.py`: loads `SELECT a.slug, a.id, a.current_submission_id, a.access_mode, c.fqdn FROM app a JOIN ... ` from Postgres into an in-memory `dict[str, AppRoute]` every 30 seconds. Supports `access_mode` changes (Phase 7) propagating within 60s.

`main.py`:
1. Lifespan: open PG pool, warm Key Vault credential, initialize `KeyVaultPublicKeyCache`, `RevokedTokenDenylistCache`, `AccessLogBatchWriter`, `AppRegistry`.
2. Starlette routes:
   - `/_shim/health`: returns 200.
   - `/_shim/metrics`: Prometheus (feature-flagged).
   - `/*` catch-all: the main handler.

Main handler flow (pseudocode):
```python
async def handle(request):
    host = request.headers["host"]
    route = app_registry.route_for_host(host)
    if route is None:
        return response_404_generic

    record_start = time.monotonic()
    token_jti = None

    # Access mode: public — skip validation, still log
    if route.access_mode == "public":
        response = await proxy.proxy_request(...)
        await audit.append(build_record(..., access_mode="public", token_jti=None))
        return response

    # Token-required: look for token via query or cookie
    query_token = request.query_params.get("rac_token")
    cookie_jti = cookie.extract_session_jti(request.cookies.get("rac_session"), settings.cookie_hmac_secret)

    if query_token:
        # First-use path: validate query token, set cookie, redirect to clean URL
        try:
            header = token.decode_unverified_header(query_token)
            public_key = await kv_key_cache.get_jwk(f"rac-app-{route.slug}-v1")
            claims = token.verify_signature_and_claims(
                query_token,
                public_key=public_key,
                expected_issuer=settings.issuer,
                expected_audience=f"rac-app:{route.slug}",
                now=datetime.now(timezone.utc),
            )
            if await denylist_cache.check(claims.jti):
                raise TokenInvalid.Revoked()
            token_jti = claims.jti
        except TokenInvalid as e:
            await audit.append(build_record(..., token_jti=None, upstream_status=None, ...))
            return render_error_response(e)
        # Set cookie, redirect to clean URL (strip rac_token)
        return redirect_with_cookie(...)

    elif cookie_jti:
        if await denylist_cache.check(cookie_jti):
            return render_error_response(TokenInvalid.Revoked())
        token_jti = cookie_jti

    else:
        # No token, no cookie → generic 403 (or a friendly "ask for a link" page)
        return render_error_response(TokenInvalid.Malformed(), code="no_token")

    # Proxy
    proxy_start = time.monotonic()
    try:
        upstream_resp = await proxy.proxy_request(...)
    except UpstreamUnavailable:
        # Cold start path
        cold_decision = cold_start.decide(upstream_resp.status, latency_ms, threshold)
        if cold_decision.should_serve_interstitial:
            asyncio.create_task(proxy.wake(route.upstream_host, httpx_client))
            return render_interstitial_response()
        raise

    latency_ms = int((time.monotonic() - proxy_start) * 1000)
    record = audit.build_record(..., token_jti=token_jti, upstream_status=upstream_resp.status_code, latency_ms=latency_ms)
    await audit.append(record)
    return upstream_resp
```

Error → error page mapping: `Revoked → error_revoked`, `Expired → error_expired`, anything else (AC7.4) → `error_generic`.

Every step logs with `structlog` stable fields. The `correlation_id` is the request-id UUID threaded into responses and logs (AC10.4).

Tests (`test_main_flow.py`, integration with testcontainers Postgres + mock-OIDC-free, a fake upstream server):
- Valid token → cookie set, 302 to clean URL, access_log row inserted with `reviewer_token_jti=<claims.jti>` (AC7.1).
- Expired token → 403 `error_expired` page; access_log row inserted with `upstream_status=NULL`, `token_jti=NULL`.
- Revoked `jti` → within 60s of insert into `revoked_token`, shim returns 403 `error_revoked` (AC7.2 — test with `freezegun` to advance time past cache TTL and then request).
- Malformed/forged token → 403 `error_generic` with NO leaked detail (AC7.4). Assert HTML body does NOT include strings like "signature", "audience", "issuer", specific error codes.
- App A token used at App B → 403 `error_generic` (AC7.6 — because it's a wrong-audience failure, which shows generic page).
- `access_mode=public` → no token needed; access_log row with `token_jti=NULL`, `access_mode='public'` (AC7.5).
- Cold upstream (first response 503 or latency > threshold) → 200 interstitial served; background wake initiated; second request within 10s succeeds via cookie → 200 from upstream (AC6.2).

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_main_flow.py -v
```

**Commit:** `feat(shim): Starlette app wiring with full token flow`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: Deployment to ACA + App Gateway backend pool update

**Verifies:** `rac-v1.AC7.1`, `rac-v1.AC7.5`, `rac-v1.AC6.2` (operational)

**Files:**
- Modify: `/home/sysop/rac/infra/main.bicep` (create a dedicated ACA app for the shim with `min-replicas=1`, user-assigned MI with Key Vault + Postgres access + role assignments)
- Create: `/home/sysop/rac/infra/modules/shim-aca-app.bicep`
- Modify: `/home/sysop/rac/infra/modules/app-gateway.bicep` (backend pool targets the shim's internal FQDN)
- Create: `apps/shim/docker-compose.dev.yml`

**Implementation:**

Shim ACA app:
- `min-replicas=1`, `max-replicas=5`, HTTP scaler with 100 concurrent requests.
- Internal ingress (public traffic arrives via App Gateway → shim → researcher apps; shim itself doesn't need external ingress because App Gateway is the public boundary).
- ENV vars wired from Key Vault references: DSN password, cookie HMAC secret, Key Vault URI.
- Managed identity has: Key Vault Crypto User (public key reads), Postgres role `rac_shim` (read on `reviewer_token`, read/batch-insert on `access_log`, read on `revoked_token`, read on `app`, read on `submission`).

App Gateway backend pool: the existing `*.${PARENT_DOMAIN}` listener now points at `<shim-slug>.internal.<env>.azurecontainerapps.io` instead of directly at researcher apps. Hostname header is preserved so the shim can resolve the target slug.

Alembic migration `0005_rac_shim_db_role.py`: creates `rac_shim` role with the grants listed above (append-only for access_log).

`docker-compose.dev.yml`: local stack with Postgres + Azure emulator stub + a fake upstream app for manual testing.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/shim-aca-app.bicep
az bicep build --file /home/sysop/rac/infra/main.bicep
uv run --project apps/shim pytest  # full suite must still pass
```

**Commit:** `feat(shim): ACA deployment + App Gateway backend update`
<!-- END_TASK_9 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_TASK_9B -->
### Task 9B: Shim OpenTelemetry metric emitters

**Verifies:** `rac-v1.AC10.2` (shim portion)

**Files:**
- Create: `apps/shim/src/rac_shim/metrics.py` (pattern: Imperative Shell)
- Create: `apps/shim/tests/test_shim_metrics.py`

**Implementation:**

`metrics.py` mirrors the Control Plane pattern (Phase 2 Task 13B). Initialize OTel `MeterProvider` with `OTLPMetricExporter` using `settings.otlp_endpoint` (default `http://localhost:4317`). Declare instruments:

```python
# pattern: Imperative Shell
from opentelemetry import metrics

_meter = metrics.get_meter("rac.shim")

token_validation_counter = _meter.create_counter(
    name="rac.shim.token_validations",
    description="Count of token validation attempts, labeled by result.",
    unit="1",
)

wake_up_duration_histogram = _meter.create_histogram(
    name="rac.shim.wake_up_duration_ms",
    description="Wall-clock ms from cold-start interstitial to upstream 200.",
    unit="ms",
)
```

**Emission call sites:**
- In `main.py`'s token validation path: after each validation outcome, call `token_validation_counter.add(1, {"result": "valid"|"expired"|"revoked"|"malformed"})`.
- In `proxy/wake_up.py`'s wake helper: after the upstream responds 200, compute elapsed ms and call `wake_up_duration_histogram.record(elapsed_ms)`.

`tests/test_shim_metrics.py`: use `InMemoryMetricReader` (same pattern as Phase 2 Task 13C):
- Assert token_validation_counter increments correctly for each result label.
- Assert wake_up_duration_histogram records a positive value when woken.

**Verification:**
```bash
uv run --project apps/shim pytest apps/shim/tests/test_shim_metrics.py -v
```

**Commit:** `feat(shim): OTel shim metric emitters (AC10.2 shim portion)`
<!-- END_TASK_9B -->

<!-- START_TASK_10 -->
### Task 10: End-to-end acceptance — reviewer access flow

**Verifies:** all Phase 6 ACs (meta)

**Files:** None

**Implementation:**

After Phase 6 deployment on dev:

1. Use a test fixture to mint a valid JWT (signed via Key Vault key created in Phase 5 for a golden app) with `aud=rac-app:<slug>`, `iss=<issuer>`, 1-hour expiry.
2. `curl -v "https://<slug>.${PARENT_DOMAIN}/?rac_token=<jwt>"` → 302 to `https://<slug>.${PARENT_DOMAIN}/` with `Set-Cookie: rac_session=... HttpOnly; Secure; SameSite=Lax`. Verify cookie flags (AC7.1).
3. Follow redirect → 200 from researcher app. SQL: `SELECT * FROM access_log WHERE reviewer_token_jti = <jti>` has a row (AC7.1, AC10.1).
4. With a token minted for App A, request App B → 403 `error_generic` (AC7.6).
5. Mint an expired token → 403 `error_expired`; HTML shows PI name + contact email (AC7.3).
6. Mint a valid token, record its jti, INSERT into `revoked_token`, wait 60 seconds, curl → 403 `error_revoked` (AC7.2).
7. Mint a forged token (wrong key) → 403 `error_generic`; assert response body does NOT contain the string "signature" or "audience" or the jti (AC7.4).
8. Set `app.access_mode='public'` for a test app → curl without token → 200 from upstream; `access_log.reviewer_token_jti IS NULL`, `access_mode='public'` (AC7.5).
9. Stop the researcher app (scale to zero explicitly), curl → interstitial HTML returned, background wake request dispatched, measure wall-clock from first interstitial to upstream 200 → must be ≤ `settings.wake_budget_seconds` (default 20s). Record the measured time in the acceptance report (AC6.2).
10. Trigger an intentional error (e.g., present a revoked token) and assert the HTTP response body contains `request_id` (AC12.2). Capture the request_id, query Log Analytics → confirm the same `request_id` appears in the structured log entry.
11. Assert the `error_generic` page body does NOT contain the strings "signature", "audience", "issuer", "traceback", "Traceback", or any internal hostname (AC12.3).

Findings → `phase6-acceptance-report.md`.

**Verification:** commands above.

**Commit:** None.
<!-- END_TASK_10 -->

---

## Phase 6 Done Checklist

- [ ] Pure token validation + cookie + routing + cold-start decision modules with property tests
- [ ] Key Vault public-key cache + denylist cache with TTL
- [ ] Async batched `access_log` writer using COPY
- [ ] httpx streaming proxy; Authorization stripped before upstream
- [ ] Branded HTML templates (interstitial + 3 error pages) with no leaked detail
- [ ] Shim Starlette app wires all pieces; structured logs; correlation_id; `datetime.now(timezone.utc)` throughout
- [ ] `rac_shim` Postgres role is used (not `rac_app`); migration 0005 creates it (Task 9)
- [ ] `settings.wake_budget_seconds = 20` defined; cold-start acceptance test asserts ≤ budget (AC6.2)
- [ ] Shim metric emitters (`rac.shim.token_validations`, `rac.shim.wake_up_duration_ms`) tested with InMemoryMetricReader (AC10.2)
- [ ] AC12.2/AC12.3 error hygiene verified: error responses carry `request_id`; no internal detail leaked
- [ ] Deployed to ACA with MI and Postgres `rac_shim` role; App Gateway routed to shim
- [ ] End-to-end acceptance on dev covers every AC7.* case + AC6.2 + AC7.5 + AC12.2 + AC12.3
- [ ] FCIS classification on every non-exempt file
