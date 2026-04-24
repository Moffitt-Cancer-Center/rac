# control-plane/backend — FastAPI Control Plane

**Freshness:** 2026-04-24

## Purpose

The authoritative API for submission intake, approval workflow, Tier 3 provisioning, reviewer token lifecycle, detection rules, access-mode toggles, cost, and access logs. Researcher and admin UIs both consume this API. Pipeline (separate repo) calls back into it via HMAC-signed webhooks.

## Package layout (`src/rac_control_plane/`)

- `main.py` — FastAPI factory `create_app()`; middleware stack (CorrelationId → Idempotency → handlers); router registration; lifespan pre-loads detection rules.
- `api/routes/` — Thin HTTP handlers, one router per resource (submissions, approvals, assets, findings, agents, webhooks, webhook_subscriptions, jobs, provisioning, ownership, cost, tokens, access_mode, access_log).
- `api/middleware/` — `IdempotencyMiddleware` (Postgres-backed, per-principal, 24h replay).
- `api/schemas/` — Pydantic request/response models. Handlers MUST declare `response_model=` to avoid accidental field leakage.
- `auth/` — Entra OIDC + client-credentials; `current_principal` dependency resolves both human and agent identities.
- `data/` — SQLAlchemy models + `get_session` / `get_session_maker`. All models use async sessions; use `TX_` prefix for functions that own a transaction.
- `services/` — Business logic per domain. Each subpackage owns one bounded context. Handlers call services; services never call HTTP.
- `detection/` — Pre-submission rule engine: `discovery.load_rules()` returns a list of rule objects; `evaluator` runs them against a `RepoContext`; starter rules live in `detection/rules/{manifest,dockerfile,repo}/`.
- `provisioning/` — Azure SDK wrappers for ACA, DNS, KV, Files. Each wrapper defines typed errors that the orchestrator maps to retry/fail decisions.
- `manifest/` — `rac.yaml` ManifestV1 schema, parser, and `form_mapper` (frontend form JSON → manifest). Used both by submission intake and by the `missing_sha` detection rule.
- `cli/` — Entrypoints for the ACA scheduled jobs (`graph_sweep_cli`, `cost_ingest_cli`).

## Key contracts

- **Principal.** `current_principal()` returns a `Principal(oid, kind, display_name, agent_id, roles)`. `kind` is `"human"` or `"agent"`. Never trust the client's `Authorization` claims — always re-validate.
- **Idempotency.** Clients send `Idempotency-Key`; middleware dedupes by `(principal_oid, key, body_hash)` for 24h. Handlers are side-effect-safe to retry but must not assume idempotency at the DB layer.
- **Reviewer tokens.** Issued by `services/tokens/issuer.py`. Flow: `claim_builder` (pure) → `jws_assembly` (pure, unsigned) → `key_probe`-derived signature format → KV `CryptographyClient.sign` → concatenate. The probe runs **once at startup** and the result is cached on app state. Never inline a new signing path.
- **Approvals.** Two-stage FSM (research → IT). Role checks enforce that the same principal cannot do both stages. Transitions are append-only in `approval_event`; `submission_id` may be null for non-submission events (ownership transfer, etc.).
- **Provisioning.** `services/provisioning/orchestrator.py` runs steps with `retry_policy`. Retry state is persisted so the `retry` admin endpoint can resume. Orchestrator is pure-logic + explicit I/O calls; retries themselves are deterministic given inputs.
- **Ownership transfer.** Preserves audit history — the old owner's `approval_event` rows stay; a new `ownership_transfer_event` records the change.

## Invariants

- **DB roles.** Backend uses the `rac_app` role which has full CRUD. It must never run queries on behalf of the shim. Migration 0009 creates `rac_shim` separately with a narrow grant set (SELECT app/app_version/revoked_token, INSERT access_log). Do not grant `rac_shim` anything else.
- **Asset finalize.** A submission cannot advance to detection-rules until every asset has a non-null `sha256`. Uploads (SAS) compute sha server-side; external URLs verify after fetch; `finalize_submission` is called only when both conditions hold. The `missing_sha` detection rule additionally blocks advancement if an external URL is still pending.
- **No raw handler DB sessions.** Handlers always use the `get_session` dependency; services take `AsyncSession` as a parameter. No module-level sessions.
- **FCIS.** Every module starts with `# pattern: Functional Core` or `# pattern: Imperative Shell`. Pure cores accept `now=`, `uuid=` injection.

## Migrations

`migrations/versions/` — Alembic, strictly forward. Current head: `0012_asset_columns_phase8`. Schema covers 15 tables: `app`, `app_version`, `submission`, `submission_asset`, `approval_event`, `agent`, `webhook_subscription`, `webhook_delivery`, `detection_finding`, `detection_finding_decision`, `scan_result`, `reviewer_token`, `revoked_token`, `access_log`, `cost_snapshot`, plus the `idempotency_key` middleware table.

## Tests

`tests/` runs against a real Postgres via the repo-level container fixture. `conftest.py` wires up the mock OIDC provider for both human and client-credentials flows. Count: 652 passing as of 2026-04-24.
