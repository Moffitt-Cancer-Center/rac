# RAC — Research Application Commons

**Freshness:** 2026-04-24 (after Phase 1–8 v1 implementation, commit 82e997b)

## What this is

Single-tenant-per-deployment platform that lets researchers submit containerized apps + small public/synthetic datasets, get them approved, built, scanned, deployed to Azure Container Apps (Tier 3), and shared via reviewer tokens or public URLs. Moffitt Cancer Center is the first deployment; the design plan is [`docs/design-plans/2026-04-23-rac-v1.md`](docs/design-plans/2026-04-23-rac-v1.md).

Each deployment is one Azure subscription hosting one Tier 2 platform (VNet, Postgres, ACR, KV, ACA env, Front Door, App Gateway, Shim) plus N Tier 3 researcher apps that come and go.

## Top-level layout

- `apps/control-plane/` — FastAPI backend (Python 3.12) + React frontend (Vite + TanStack Router/Query). This is where all researcher-facing and admin workflows live.
- `apps/shim/` — Starlette + httpx reverse proxy ("Token-Check Shim"). Sits between App Gateway and researcher ACA apps; enforces reviewer token / cookie / access-mode; writes access logs.
- `infra/` — Bicep IaC for the Tier 2 platform. See `infra/CLAUDE.md` for the two-pass deploy.
- `docs/runbooks/` — Operational procedures: bootstrap, incident-response, siem-export, cost-control, orphan-blob-cleanup.
- Pipeline repo is a **separate sibling repo** (`rac-pipeline`), not a subdirectory. It must live outside this repo so researcher Dockerfiles never execute against this codebase. See its own CLAUDE.md.

## Cross-cutting architectural decisions

**Control Plane and Shim share one Postgres database but use different DB roles.** `rac_app` (control plane) has read/write across all tables. `rac_shim` has SELECT on `app` + `app_version` + `revoked_token` and INSERT on `access_log` only. This is enforced by migration `0009_rac_shim_db_role` and is the principal defense against shim compromise turning into a control-plane compromise.

**Reviewer tokens are JWS signed by Key Vault.** Private key never leaves KV. Control plane assembles the JWS header/payload as pure code, calls `CryptographyClient.sign` for the signature, and concatenates. Signature format (raw IEEE P1363 vs DER) varies by KV algorithm and is probed at control-plane startup via `key_probe.py`; the discovered format is cached and used for subsequent signings. Shim verifies signatures using a public-key cache (`KeyVaultPublicKeyCache`) with TTL. Never re-implement the probe ad-hoc — reuse the module.

**Assets use one ACA volume with sub_paths.** Every researcher app mounts the same Azure Files share at `/data/rac` but each asset gets its own subdirectory (`sub_path`). This keeps the IaC simple (one volume per app) while giving per-asset paths in the container. The copy pipeline is Blob-staging → Files-copy → finalize-submission signal. Do not design alternative mount strategies.

**Submission finalize is signal-triggered.** `finalize_submission` is called *only* after every asset has a recorded sha256 (uploaded or verified). The `missing_sha` detection rule blocks submissions where an external URL asset has not completed its fetch+verify. This preserves the invariant: no submission advances past intake until every asset is content-addressable.

**Idempotency is middleware, not per-handler.** `IdempotencyMiddleware` sits on the ASGI stack with its own session factory (not request-scoped DI); keyed by `Idempotency-Key` header + principal OID + body hash; replays 24h. Handlers write business logic without worrying about duplicates.

**Everything is correlated.** `CorrelationIdMiddleware` extracts or generates an `X-Correlation-Id`; structlog binds it to every log line; all ApiError responses include it. The shim generates its own correlation id per request and forwards it on proxied responses.

## FCIS discipline

All `# pattern: Functional Core` modules must stay pure: no I/O, no datetime.now, no uuid4, no DB. The shell passes `now=`, `record_id=`, session, pool, etc. Code review is strict on this.

## Test footprint

652 backend + 84 frontend + 70 rac-pipeline tests must stay green. Tests use a real Postgres (container fixture) plus a mocked OIDC IdP; there is no in-memory ORM fake.

## See also

- `infra/CLAUDE.md` — Bicep platform, two-pass deploy gotchas
- `apps/control-plane/backend/CLAUDE.md` — domain map, DB role separation, auth model
- `apps/shim/CLAUDE.md` — request flow, denylist semantics, cold-start interstitial
- `docs/runbooks/bootstrap.md` — end-to-end first-deploy walkthrough
