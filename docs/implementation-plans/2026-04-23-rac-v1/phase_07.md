# Phase 7: Reviewer token management and post-publication public mode

**Goal:** Researchers can mint, label, list, and revoke reviewer tokens through the Control Plane UI. Access logs are visible to the owning researcher and admins. Admins can flip `access_mode` between `token_required` and `public`. The Shim's denylist cache (Phase 6) picks up revocations within 60 seconds; the app_registry cache picks up `access_mode` changes within 30 seconds (design says 60s — the shorter interval is fine, just faster).

**Architecture:** FCIS. Pure: JWS assembly (header + payload base64url encoding), ES256 r||s signature extraction from Key Vault DER output, reviewer-token claim building, access_mode toggle validation. Shell: Key Vault `sign` operation, Postgres writes to `reviewer_token`, `revoked_token`, `app.access_mode`, `approval_event`. React features for token lifecycle and access-log viewer. No new Azure resources; uses Phase 5's per-app signing keys and Phase 6's denylist protocol.

**Tech Stack:** FastAPI + Pydantic (Control Plane), `azure-keyvault-keys.CryptographyClient.sign`, `cryptography.hazmat.primitives.asymmetric.utils.decode_dss_signature` for DER-to-r||s conversion. React + TanStack Query + TanStack Router.

**Scope:** Phase 7 of 8.

**Codebase verified:** 2026-04-23 — Phase 5 delivered per-app signing keys in Key Vault; Phase 6 delivered the Shim consuming those public keys and checking the `revoked_token` table. `reviewer_token` and `revoked_token` tables from Phase 2 migration. `Control Plane` has no token-mint endpoint yet; `access_mode` column on `app` defaults to `'token_required'` in Phase 2 migration.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC7: Reviewer access works via tokenized URLs (issuance + revocation portion)
- **rac-v1.AC7.1 Success (issuance portion):** A valid token presented via URL query sets an HttpOnly/Secure/SameSite=Lax cookie… — this phase mints the token; the Shim validates it. Paired tests live in both phases.
- **rac-v1.AC7.2 Success (revocation portion):** A revoked token (`jti` present in `revoked_token`) is rejected within 60 seconds of revocation — Control Plane writes to `revoked_token`; the Shim's cache picks up within TTL.
- **rac-v1.AC7.5 Edge:** An app in `access_mode=public` serves all requests without token validation; `access_log` rows are still written with `token_jti=NULL` — toggle endpoint lives here.

### rac-v1.AC12: Cross-cutting audit (applies to new token/access_mode events)
- **rac-v1.AC12.1:** `reviewer_token` and `revoked_token` writes go through the append-only path; no UPDATE/DELETE grants.

**Verifies:** Functionality phase. Each task names which AC cases it tests.

---

## File Classification Policy

- `services/tokens/jws_assembly.py`, `claim_builder.py`, `signature_decode.py`: Functional Core.
- `services/tokens/issuer.py`: Imperative Shell (Key Vault sign operation).
- `services/tokens/revoke.py`: Imperative Shell (DB write).
- `services/access_mode/toggle.py`: Imperative Shell.
- `api/routes/tokens.py`, `access_mode.py`: Imperative Shell.
- React features: per Phase 2 conventions (pure render + Zod schemas; data fetch is the shell boundary).

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Pure JWS assembly + signature decoding

**Verifies:** Foundation for `rac-v1.AC7.1` (issuance)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/claim_builder.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/jws_assembly.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/signature_decode.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_claim_builder.py`
- Create: `apps/control-plane/backend/tests/test_jws_assembly.py`
- Create: `apps/control-plane/backend/tests/test_signature_decode.py`

**Implementation:**

`claim_builder.py` (pure):
- `def build_reviewer_claims(*, app_slug: str, reviewer_label: str, issuer: str, issued_at: datetime, expires_at: datetime, jti: UUID, scope: str = "read") -> dict`:
  ```python
  return {
      "iss": issuer,
      "aud": f"rac-app:{app_slug}",
      "sub": reviewer_label,       # user-chosen label, e.g. "Journal Reviewer #3"
      "jti": str(jti),
      "iat": int(issued_at.timestamp()),
      "exp": int(expires_at.timestamp()),
      "scope": scope,
  }
  ```
- Property tests: claims always contain the 7 required fields; `exp > iat`; `jti` valid UUID.

`jws_assembly.py` (pure):
- `def base64url_encode(data: bytes) -> str`: RFC 7515 URL-safe Base64 without padding.
- `def build_signing_input(header: dict, payload: dict) -> tuple[str, bytes]`: returns the compact form prefix `"{b64_header}.{b64_payload}"` as str AND the bytes to be signed (UTF-8 of that string). Deterministic JSON serialization via `json.dumps(..., separators=(',', ':'), sort_keys=True)` to ensure signature reproducibility.
- `def assemble_jws(signing_input: str, signature: bytes) -> str`: `"{signing_input}.{base64url_encode(signature)}"`.
- Property tests: decoded header/payload from the assembled JWS round-trip to the original inputs.

`signature_decode.py` (pure):
- `def der_to_raw_r_s(der: bytes, coord_size: int = 32) -> bytes`: Key Vault's `CryptographyClient.sign(es256, ...)` returns raw r||s (not DER) — but confirm this against the SDK docs; if DER, use `cryptography.hazmat.primitives.asymmetric.utils.decode_dss_signature` then `int.to_bytes(coord_size, 'big')` for r and s. Keep both code paths + a flag; decided by a small "probe on startup" that asks Key Vault to sign a known input and checks the output length (64 bytes = r||s; ~70 bytes = DER).
- Property test: given random 32-byte r and 32-byte s, `der_to_raw_r_s(encode_dss_signature(r, s))` returns `r||s`.

Rationale: the 2026 research noted Key Vault may return DER; the probe-on-startup is a small piece of belt-and-suspenders so we don't hard-code the wrong assumption. Document which mode was detected in the logs on startup.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_claim_builder.py apps/control-plane/backend/tests/test_jws_assembly.py apps/control-plane/backend/tests/test_signature_decode.py -v
```

**Commit:** `feat(control-plane): pure JWS assembly + claim builder + signature decode`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Token issuer (Shell — Key Vault sign operation)

**Verifies:** `rac-v1.AC7.1` (issuance)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/issuer.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/key_probe.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_token_issuer.py`

**Implementation:**

`key_probe.py`: `async def detect_signature_format(kv_crypto_client) -> SignatureFormat`: signs a fixed 32-byte digest; checks output length. Result is **cached as a module-level variable set once at startup** (in the FastAPI lifespan); it is NOT invalidated between requests. The probe re-fires only when the service restarts — this is intentional, because Key Vault's signature format is a property of the HSM firmware version, not of the key or request. If the format ever changes (e.g., after a Key Vault upgrade), the operator must restart the Control Plane container; document this in `docs/runbooks/bootstrap.md` under "Key Vault signing format probe." Log the detected format at INFO level on startup: `"Key Vault signing format detected: {format}"` so operators can verify the probe outcome in Log Analytics without needing to redeploy.

`issuer.py`:
- `async def issue_reviewer_token(session, *, app_id: UUID, app_slug: str, reviewer_label: str, ttl_days: int, actor_principal_id: UUID) -> IssuedToken`:
  1. Pure: validate `ttl_days <= settings.max_reviewer_token_ttl_days` (default 180, per design).
  2. Generate `jti = uuid.uuid4()` (NOT UUIDv7 — we don't want time-ordered jtis leaking issuance time).
  3. Compute `iat=now()`, `exp=now() + ttl_days days`.
  4. Pure: `claims = claim_builder.build_reviewer_claims(...)`, `header = {"alg": "ES256", "typ": "JWT", "kid": f"rac-app-{app_slug}-v1"}`, `(signing_input_str, signing_input_bytes) = jws_assembly.build_signing_input(header, claims)`.
  5. Compute digest: `hashlib.sha256(signing_input_bytes).digest()`.
  6. Shell: `crypto_client = CryptographyClient(key=kv_client.get_key(f"rac-app-{app_slug}-v1"), credential=...)`.
  7. Shell: `sig_result = await crypto_client.sign(SignatureAlgorithm.es256, digest)`.
  8. Pure: if detected format is DER → `raw = der_to_raw_r_s(sig_result.signature)`; else `raw = sig_result.signature`.
  9. Pure: `jws = assemble_jws(signing_input_str, raw)`.
  10. Shell: INSERT into `reviewer_token` with `id=jti`, `app_id`, `reviewer_label`, `kid=header.kid`, `issued_by_principal_id=actor_principal_id`, `expires_at=exp`, `scope='read'`.
  11. Shell: INSERT into `approval_event(kind='reviewer_token_issued', ...)`.
  12. Return `IssuedToken(jwt=jws, jti=jti, expires_at=exp, reviewer_label=reviewer_label)`.

`tests/test_token_issuer.py` (integration — uses a test harness that calls a real Key Vault **emulator** — e.g., `mcr.microsoft.com/azurekeyvault-emulator` if available, or a mock of `CryptographyClient` that actually signs with a test EC P-256 key locally):
- Issuance produces a JWS where the signature verifies against the expected public key (roundtrip).
- Decoded claims match what was requested.
- `reviewer_token` row inserted with correct fields.
- TTL > max → `ValidationApiError`.
- DER-mode vs raw-mode detection both work (test both by mocking the signer to return either shape).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_token_issuer.py -v
```

**Commit:** `feat(control-plane): reviewer token issuer via Key Vault sign`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Revoke + list services

**Verifies:** `rac-v1.AC7.2` (revocation)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/revoke.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/tokens/listing.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_token_revoke.py`

**Implementation:**

`revoke.py`:
- `async def revoke_token(session, *, jti: UUID, actor_principal_id: UUID, reason: str | None) -> None`:
  1. Look up `reviewer_token` by `jti`; 404 if missing.
  2. Authorization check (caller's responsibility — route layer enforces) already done; service is a pure action.
  3. INSERT into `revoked_token(jti, revoked_by_principal_id=actor_principal_id, reason, expires_at=<reviewer_token.expires_at>, created_at=now)` — append-only; the `expires_at` on the denylist row matches the token's natural expiry so the Shim's cache can drop it after.
  4. INSERT into `approval_event(kind='reviewer_token_revoked', ...)`.

`listing.py`:
- `async def list_tokens_for_app(session, *, app_id, include_revoked=False) -> list[TokenListRow]`. Joins `reviewer_token` ↔ `revoked_token` on `jti`. Returns a DTO with `jti`, `reviewer_label`, `issued_at`, `expires_at`, `revoked_at`, `scope`.
- Useful admin-level: `async def list_tokens_for_reviewer(session, *, reviewer_label_pattern)`.

Tests:
- Revoke existing token → `revoked_token` row exists; `approval_event` row exists.
- Revoke nonexistent `jti` → `NotFoundError`.
- List shows revoked rows with `revoked_at` set.
- `AC12.1` guard: direct UPDATE on `revoked_token` via the application DB role is denied (already asserted by Phase 2 test; re-assert here with the new rows).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_token_revoke.py -v
```

**Commit:** `feat(control-plane): token revoke + listing services`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-5) -->

<!-- START_TASK_4 -->
### Task 4: Token API routes

**Verifies:** `rac-v1.AC7.1`, `rac-v1.AC7.2`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/tokens.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/schemas/tokens.py`
- Create: `apps/control-plane/backend/tests/test_tokens_api.py`

**Implementation:**

Endpoints (all auth required):
- `POST /apps/{app_id}/tokens`: body `{reviewer_label: str, ttl_days: int}`. Auth: submitter (the researcher who owns the submission — checked via `submission.submitter_principal_id == principal.oid`), or admin. Returns `{jwt, jti, expires_at, visit_url}` where `visit_url = "https://{slug}.{parent_domain}/?rac_token={jwt}"`. **The `jwt` field is returned ONCE on this response; there is no re-fetch endpoint.** The UI must copy the URL at creation time.
- `GET /apps/{app_id}/tokens`: auth same; returns `TokenListRow[]` (without the raw JWT — only metadata).
- `DELETE /apps/{app_id}/tokens/{jti}`: revoke. Auth same. Returns 204 on success, 404 on unknown `jti`.

Input validation:
- `reviewer_label`: 1–100 chars, non-whitespace.
- `ttl_days`: 1–180 (design default).

Tests:
- Submitter creates a token → 201 with jwt; the jwt validates against the Shim's public key (mock call to `verify_signature_and_claims` with the test key).
- Listing shows the new token without the jwt.
- Revoking it inserts into `revoked_token`; subsequent listing shows `revoked_at`.
- Non-owner non-admin creates → 403.
- `ttl_days=181` → 422.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_tokens_api.py -v
```

**Commit:** `feat(control-plane): token API (mint, list, revoke)`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Access mode toggle API + service

**Verifies:** `rac-v1.AC7.5`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/access_mode/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/access_mode/toggle.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/access_mode/validation.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/access_mode.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_access_mode_toggle.py`

**Implementation:**

`validation.py` (pure):
- `def can_set_public(app: App, principal: Principal, now: datetime) -> ValidationResult`:
  - App must be `deployed` (not mid-provisioning).
  - Principal must be admin OR the app's owning researcher (submitter principal on the current submission).
  - Optional post-publication gate (per design intent): only allow public mode if the submission has a `publication_doi` recorded. In v1 we parameterize this: `settings.require_publication_for_public = False` by default; institution can turn on.
- `def can_set_token_required(app: App, principal: Principal) -> ValidationResult`: always allowed by owner or admin.
- Pure; property test: public→token_required is always allowed by owner; token_required→public is gated.

`toggle.py`:
- `async def set_access_mode(session, *, app_id: UUID, new_mode: Literal['token_required','public'], actor_principal_id: UUID, admin: bool, notes: str | None) -> None`:
  1. Load `app`, `principal`.
  2. Pure: `validation.can_set_<mode>` — on invalid, raise `ForbiddenError` or `ValidationApiError` with the specific reason.
  3. UPDATE `app.access_mode = new_mode`. (This is the ONE table where `app.access_mode` is mutated — the `app` table is not append-only; this is by design.)
  4. INSERT `approval_event(kind='access_mode_changed', actor_principal_id, detail={from, to, notes}, submission_id=app.current_submission_id)`.
  5. The shim picks up the change within its app_registry refresh interval (30s by default — design said 60s; tighter is fine).

Route: `POST /apps/{app_id}/access-mode` with body `{mode: "public"|"token_required", notes?: str}`. Admin or owning researcher.

Tests:
- Owner sets `public` on a deployed app → succeeds; `app.access_mode='public'`; `approval_event` row exists.
- Non-owner non-admin → 403.
- Setting on an `approved`-but-not-`deployed` app → 422.
- Flip back to `token_required` → succeeds.
- Shim integration: bring up a dev Shim pointing at the test DB; set `public`; within 30s, an unauthenticated request to the app returns 200 (AC7.5 end-to-end here, not just UI).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_access_mode_toggle.py -v
```

**Commit:** `feat(control-plane): access_mode toggle API`
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 6-8) -->

<!-- START_TASK_6 -->
### Task 6: Access log viewer service + API

**Verifies:** None directly (UI for existing data); enables AC7.* verification

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/access_log/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/access_log/query.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/access_log.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_access_log_api.py`

**Implementation:**

Paginated, filterable access log query:
- `GET /apps/{app_id}/access-log?before=<uuid>&limit=50&mode=<filter>&jti=<filter>&status=<filter>` — keyset pagination on `access_log.id` (UUIDv7 is time-ordered; `before=<cursor>` = `id < cursor`).
- Response: `items[].{id, created_at, reviewer_token_jti, reviewer_label, access_mode, method, path, upstream_status, latency_ms, source_ip}`. Joined to `reviewer_token` to include `reviewer_label`.
- Auth: owning researcher or admin.
- Returns at most 100 rows per page.

Tests: pagination, filters, permission.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_access_log_api.py -v
```

**Commit:** `feat(control-plane): access log viewer API`
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: React features — token management, access log, access mode toggle

**Verifies:** `rac-v1.AC7.1` (UI), `rac-v1.AC7.2` (UI), `rac-v1.AC7.5` (UI)

**Files:**
- Create: `apps/control-plane/frontend/src/features/tokens/mint-dialog.tsx`
- Create: `apps/control-plane/frontend/src/features/tokens/tokens-table.tsx`
- Create: `apps/control-plane/frontend/src/features/tokens/one-shot-url-display.tsx`
- Create: `apps/control-plane/frontend/src/features/access-log/access-log-viewer.tsx`
- Create: `apps/control-plane/frontend/src/features/admin/access-mode/toggle-card.tsx`
- Create: `apps/control-plane/frontend/src/routes/apps/$appId/tokens.tsx`
- Create: `apps/control-plane/frontend/src/routes/apps/$appId/access-log.tsx`
- Create: `apps/control-plane/frontend/src/tests/tokens.test.tsx`

**Implementation:**

`mint-dialog.tsx`: form with `reviewer_label` + `ttl_days` (default 30, dropdown: 7, 30, 90, 180). On successful POST, render `<OneShotUrlDisplay>` with:
- Big "Copy" button for the full URL (`https://{slug}.{parent_domain}/?rac_token=...`).
- A warning that this URL is shown **once** — closing the dialog without copying permanently loses access; the token will remain valid but the raw value cannot be retrieved.
- Timeout after 5 minutes to auto-clear the visible URL from DOM (mitigates shoulder-surfing).

`tokens-table.tsx`: lists all tokens for the app, columns: label, issued at, expires at, revoked at (or active badge), issued-by principal, revoke button per row.

`access-log-viewer.tsx`: paginated table using TanStack Query's `useInfiniteQuery` with keyset pagination cursor. Filter dropdowns for mode, status, jti. Shows reviewer_label when available.

`toggle-card.tsx`: radio buttons for `token_required`/`public` with a required "Reason" textarea; submit button disabled until reason provided. Displays a confirmation dialog before flipping to `public` with a clear warning.

Tests: minting renders the one-shot display; clicking revoke calls DELETE; filter changes trigger re-query.

**Verification:**
```bash
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test
```

**Commit:** `feat(control-plane): token mint/revoke UI, access log viewer, access mode toggle`
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: End-to-end acceptance — token lifecycle + public mode

**Verifies:** All Phase 7 ACs (meta)

**Files:** None

**Implementation:**

Against dev (Control Plane + Shim both deployed):

1. Owning researcher logs into the UI, mints a token with label "Reviewer #1", ttl 30 days. UI shows the one-shot URL. Copy it.
2. `curl -v "<the-url>"` → 302 with Set-Cookie, follow → 200 from the researcher app. `access_log` row has `reviewer_token_jti`, `reviewer_label="Reviewer #1"` (AC7.1).
3. Researcher clicks Revoke on the token row. Wait 60s. `curl` using the same URL → 403 revoked page (AC7.2).
4. Admin toggles `access_mode=public`. Within 30s, `curl` without token → 200 from researcher app; `access_log.reviewer_token_jti IS NULL`, `access_mode='public'` (AC7.5).
5. Admin toggles back to `token_required` → within 30s, unauthenticated requests → 403.
6. Non-owner attempts to mint → 403.
7. Non-owner attempts to toggle access_mode → 403.

Findings → `phase7-acceptance-report.md`.

**Verification:** commands above.

**Commit:** None.
<!-- END_TASK_8 -->

<!-- END_SUBCOMPONENT_C -->

---

## Phase 7 Done Checklist

- [ ] Pure JWS assembly + claim builder + signature decode have property tests
- [ ] Issuer signs via Key Vault and produces JWS verifiable against public key
- [ ] Key format probe correctly detects DER vs raw and converts if needed
- [ ] Revoke writes append-only row; no UPDATE/DELETE paths
- [ ] Token mint/list/revoke API endpoints work with owner + admin auth
- [ ] Access mode toggle API works; validation is pure; admin + owner allowed
- [ ] Access log viewer paginates via keyset cursors; filters work
- [ ] Frontend token, access-log, and access-mode features pass vitest
- [ ] End-to-end acceptance on dev covers AC7.1, AC7.2, AC7.5
- [ ] FCIS classification on every non-exempt file
