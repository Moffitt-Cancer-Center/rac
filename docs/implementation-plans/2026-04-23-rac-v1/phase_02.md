# Phase 2: Control Plane skeleton — auth, schema, submission CRUD

**Goal:** A FastAPI Control Plane that authenticates researchers via Entra OIDC, accepts OAuth2 client-credentials for service-to-service access, exposes typed submission CRUD with idempotency semantics, persists to Postgres, and ships a small React + TypeScript frontend with an authenticated submission form. Pipeline dispatch is stubbed; the submission record is created at `awaiting_scan` and stays there until Phase 3.

**Architecture:** Functional core / imperative shell (FCIS). Pure modules: submission FSM transitions, slug derivation, idempotency-key hashing, Pydantic schemas. Imperative shell: Alembic migrations, SQLAlchemy async session, OIDC middleware, FastAPI routes, Azure-SDK-adjacent Key Vault and Storage access (stubbed behind thin wrappers this phase). Frontend is a Vite + React + TypeScript + TanStack Router + TanStack Query SPA using `@azure/msal-react` (PKCE) for Entra SSO, packaged into the same Docker image under `/static/`.

**Tech Stack:** FastAPI 0.136+, Pydantic v2.7+, SQLAlchemy 2.x async with asyncpg, Alembic, `fastapi-azure-auth` for Entra JWKS validation, `asgi-idempotency-header` (Redis-free in-memory default; Postgres-backed custom store), `edwh-uuid7` for client-side UUIDv7, `testcontainers[postgres]` + `mock-oidc` for tests, Vite + React 18 + TypeScript + TanStack Router/Query + `@azure/msal-react` + React Hook Form + Zod. Python tooling via `uv` workspace; frontend via `pnpm`.

**Scope:** Phase 2 of 8 from the original design.

**Codebase verified:** 2026-04-23 — greenfield; no `apps/control-plane/`, no `pyproject.toml`, no existing Python or TypeScript sources. Phase 1 delivers the Azure platform and tagged resource group naming conventions used here.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC2: Researchers can submit applications end-to-end (partial — web UI intake, not pipeline)
- **rac-v1.AC2.1 Success:** An authenticated researcher submits a valid GitHub URL + Dockerfile path via the web UI; a `submission` row is created with `status=awaiting_scan`, the correct `submitter_principal_id`, and a slug derived from the paper metadata.
- **rac-v1.AC2.3 Failure:** Unauthenticated request to `POST /submissions` returns 401.
- **rac-v1.AC2.4 Failure:** Submission with a GitHub URL that returns 404 surfaces a validation error before the pipeline is dispatched.
- **rac-v1.AC2.6 Edge:** The researcher's Entra object ID (`oid`) is persisted as `submitter_principal_id` and used consistently across `submission`, `approval_event`, and `detection_finding` rows.

### rac-v1.AC3: Submission is API-first (partial — auth modes and idempotency)
- **rac-v1.AC3.1 Success:** OAuth2 client credentials auth with a registered `agent` grants access to submission endpoints; `agent_id` is recorded on every submission created via this path.
- **rac-v1.AC3.2 Success:** A duplicate `POST /submissions` with the same `Idempotency-Key` returns the original `submission_id` and HTTP 200 (not a new row).
- **rac-v1.AC3.5 Failure:** Request from a disabled `agent` (via client credentials) returns 403.

### rac-v1.AC12: Cross-cutting audit and error hygiene (established here for the whole Control Plane)
- **rac-v1.AC12.1:** Append-only tables (`approval_event`, `detection_finding`, `access_log`, `revoked_token`) have no `UPDATE`/`DELETE` grants to the application DB role.
- **rac-v1.AC12.2:** Every API error response body includes a `correlation_id` that is also present in the corresponding App Insights trace for that request.
- **rac-v1.AC12.3:** API error responses never leak Postgres error text, stack traces, or internal URIs to the client.

### rac-v1.AC10: Observability — Control Plane custom metrics (partial — submission and approval counters)
- **rac-v1.AC10.2 Success (partial):** Custom metrics `rac.submissions.by_status` (counter, labeled by `status`) and `rac.approvals.time_to_decision_seconds` (histogram) are emitted to Azure Monitor via OpenTelemetry at each FSM transition. The `rac.scans.verdict` counter and `rac.shim.*` metrics are owned by Phase 3 and Phase 6 respectively.

**Verifies:** Functionality phase. Each task names which AC cases it tests.

---

## File Classification Policy

Every Python file with runtime behavior (functions, classes with methods, orchestration) MUST include a `# pattern: Functional Core` or `# pattern: Imperative Shell` comment per the house FCIS skill. Type-only modules (Pydantic models that only declare fields), `__init__.py` barrels, tests, and generated files are exempt. TypeScript follows the same pattern using `// pattern: Functional Core` / `// pattern: Imperative Shell`.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Python + frontend monorepo scaffold (pyproject, uv, vite)

**Verifies:** None (setup)

**Files:**
- Create: `/home/sysop/rac/pyproject.toml` (workspace root)
- Create: `/home/sysop/rac/uv.lock` (generated by `uv lock`)
- Create: `/home/sysop/rac/apps/control-plane/backend/pyproject.toml` (workspace member)
- Create: `/home/sysop/rac/apps/control-plane/backend/src/rac_control_plane/__init__.py`
- Create: `/home/sysop/rac/apps/control-plane/backend/tests/__init__.py`
- Create: `/home/sysop/rac/apps/control-plane/backend/conftest.py`
- Create: `/home/sysop/rac/apps/control-plane/frontend/package.json`
- Create: `/home/sysop/rac/apps/control-plane/frontend/pnpm-lock.yaml` (generated)
- Create: `/home/sysop/rac/apps/control-plane/frontend/tsconfig.json`
- Create: `/home/sysop/rac/apps/control-plane/frontend/vite.config.ts`
- Create: `/home/sysop/rac/apps/control-plane/frontend/index.html`
- Create: `/home/sysop/rac/apps/control-plane/frontend/src/main.tsx`

**Implementation:**

Root `pyproject.toml` declares a `uv` workspace: `[tool.uv.workspace] members = ["apps/control-plane/backend"]`. Python 3.12 required.

`apps/control-plane/backend/pyproject.toml` declares the control plane package `rac-control-plane`, dependencies (`fastapi>=0.136`, `pydantic>=2.7`, `sqlalchemy>=2.0`, `alembic`, `asyncpg`, `uvicorn[standard]`, `gunicorn`, `fastapi-azure-auth`, `asgi-idempotency-header`, `edwh-uuid7`, `pyyaml`, `pydantic-settings`, `httpx`, `structlog`, `azure-identity`, `azure-keyvault-keys`, `azure-keyvault-secrets`, `azure-storage-blob`, `opencensus-ext-azure`), dev dependencies (`pytest`, `pytest-asyncio`, `pytest-cov`, `testcontainers[postgres]`, `httpx`, `ruff`, `mypy`). Set `[tool.ruff]` rules (`E`, `F`, `W`, `I`, `B`, `UP`, `N`, `S`), `[tool.mypy] strict = true`.

`conftest.py` declares `pytest_plugins = ["tests.fixtures.db", "tests.fixtures.oidc"]` — fixture modules added in later tasks.

`frontend/package.json`: name `@rac/control-plane-frontend`, Vite + React 18 + TypeScript scaffold. Dependencies: `react`, `react-dom`, `@tanstack/react-router`, `@tanstack/react-query`, `@azure/msal-browser`, `@azure/msal-react`, `react-hook-form`, `@hookform/resolvers`, `zod`. Dev: `vite`, `@vitejs/plugin-react`, `typescript`, `@tanstack/router-plugin`, `@types/react`, `eslint`, `prettier`. Scripts: `dev`, `build`, `preview`, `lint`, `typecheck`, `test` (vitest).

`vite.config.ts` wires `@tanstack/router-plugin/vite` before `@vitejs/plugin-react`. Outputs to `../backend/src/rac_control_plane/static/` so FastAPI can serve the built SPA from the same image.

Both `main.py` and `main.tsx` are stub entrypoints that exit cleanly (FastAPI `app = FastAPI(title="RAC Control Plane")` with a `/health` endpoint returning `{"status":"healthy"}`; React renders an empty `<App />` that prints "RAC" and the environment name from an env var).

**Verification:**
```bash
cd /home/sysop/rac
uv sync
uv run --project apps/control-plane/backend pytest --collect-only  # zero tests but collection must succeed
uv run --project apps/control-plane/backend ruff check
uv run --project apps/control-plane/backend mypy apps/control-plane/backend/src
cd apps/control-plane/frontend && pnpm install && pnpm build && pnpm typecheck
```

**Commit:** `feat(control-plane): monorepo scaffold (uv workspace + vite)`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Control Plane settings + structured logging (Imperative Shell)

**Verifies:** Supports `rac-v1.AC12.2`, `rac-v1.AC12.3`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/settings.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/logging_setup.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/correlation.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_settings.py`

**Implementation:**

`settings.py` uses `pydantic_settings.BaseSettings` with env-var prefix `RAC_`. Fields (grouped): deployment (`env: Literal['dev','staging','prod']`, `institution_name: str`, `parent_domain: str`, `brand_logo_url: str`); IdP (`idp_tenant_id: str`, `idp_client_id: str`, `idp_api_client_id: str`); database (`pg_host: str`, `pg_port: int = 5432`, `pg_db: str`, `pg_user: str`, `pg_password: SecretStr`, `pg_ssl_mode: str = 'require'`); Azure (`kv_uri: str`, `blob_account_url: str`, `acr_login_server: str`, `aca_env_resource_id: str`); scans (`scan_severity_gate: Literal['critical','high','medium','low']`); approvers (`approver_role_research: str`, `approver_role_it: str`); webhooks (`webhook_secret_rotation_days: int = 30`). All values are required except with explicit defaults. Provide `get_settings()` cached via `@functools.lru_cache`.

`logging_setup.py` configures `structlog` with JSON output, stable fields (`submission_id`, `app_id`, `principal_id`, `request_id`, `correlation_id`), and App Insights handler via `opencensus-ext-azure` when `APPLICATIONINSIGHTS_CONNECTION_STRING` is present. Logger is attached at app startup (invoked in `lifespan`).

`correlation.py` provides a `CorrelationIdMiddleware` (ASGI middleware) that reads/generates `X-Request-Id` per request, binds it into `structlog.contextvars`, and echoes it in every response header. The error handler (Task 4) uses this to set `correlation_id` in every error response body.

`tests/test_settings.py`: pure asserts that `get_settings()` raises `ValidationError` when required fields are missing; positive case that it parses a complete `.env.example`.

**Verification:**
```bash
cd /home/sysop/rac
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_settings.py -v
```

**Commit:** `feat(control-plane): settings + structured logging + correlation id`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: FastAPI app skeleton with lifespan + error handler

**Verifies:** `rac-v1.AC12.2`, `rac-v1.AC12.3`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/main.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/errors.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_errors.py`
- Create: `apps/control-plane/backend/tests/test_app_health.py`

**Implementation:**

`errors.py` (pure) defines typed error classes (`ApiError`, `NotFoundError`, `ValidationApiError`, `AuthError`, `ForbiddenError`, `ConflictError`) each carrying `code: str`, `http_status: int`, `public_message: str` (safe to show users). Defines a pure function `render_error(exc: ApiError, correlation_id: str) -> dict` that returns `{"code": ..., "message": ..., "correlation_id": ...}` — no stack traces, no Postgres error text, no internal URIs.

`main.py` wires:
- `@asynccontextmanager async def lifespan(app)` — opens the SQLAlchemy async engine, initializes logging, warms up the Azure credentials, cleans up on exit.
- `FastAPI(lifespan=lifespan, title="RAC Control Plane", version="1.0.0")`
- Middleware stack (order matters): CorrelationIdMiddleware → `IdempotencyHeaderMiddleware` (Task 9) → auth middleware (Tasks 5-6)
- Global exception handlers: `ApiError` → `render_error`, `HTTPException` → `render_error(ApiError(...))`, `Exception` (catch-all) → log with traceback, return generic 500 with correlation_id and no internal details
- Static file mount at `/static/` serving the built React SPA
- OpenAPI customization: title, description lifted from the design plan Summary section, tags per route group, servers per env
- Health route: `GET /health` → `{"status":"healthy","version":"1.0.0","env": settings.env}`

`tests/test_errors.py` (pure): assert `render_error` for each exception class produces exactly the expected dict shape and never includes `str(exc)` of the internal exception. Property test with Hypothesis if easy: given any exception, output contains `correlation_id` and has exactly the keys `{code, message, correlation_id}`.

`tests/test_app_health.py` (integration with httpx AsyncClient and the FastAPI app): `GET /health` returns 200 and expected body. An unhandled internal error path (triggered by a test-only route that raises `RuntimeError`) returns a 500 with no stack trace or internal detail in body, and `X-Request-Id` header echoed.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_errors.py apps/control-plane/backend/tests/test_app_health.py -v
```

**Commit:** `feat(control-plane): FastAPI skeleton with lifespan and safe error handler`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-7) -->

<!-- START_TASK_4 -->
### Task 4: Postgres schema + Alembic setup (all v1 tables)

**Verifies:** Baseline for `rac-v1.AC2.1`, `rac-v1.AC3.*`, `rac-v1.AC12.1`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/data/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/data/db.py` (pattern: Imperative Shell) — async engine, sessionmaker, dependency
- Create: `apps/control-plane/backend/src/rac_control_plane/data/models.py` (pattern: Imperative Shell — SQLAlchemy ORM `Base` with mapped classes. Although field declarations look type-like, mapped classes carry session-coupled behavior: lazy loads, identity map, `expire_on_commit`. Per the cross-phase README decision, ORM models are always Imperative Shell. Tests must use `async_sessionmaker(expire_on_commit=False)` and avoid triggering lazy loads.)
- Create: `apps/control-plane/backend/alembic.ini`
- Create: `apps/control-plane/backend/migrations/env.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/migrations/versions/0001_initial_schema.py`
- Create: `apps/control-plane/backend/tests/test_schema_migration.py`

**Implementation:**

`data/db.py` constructs `create_async_engine(settings.pg_dsn, echo=False)`, `async_sessionmaker(expire_on_commit=False)`, and a FastAPI dependency `get_session()` yielding an `AsyncSession` per request.

`data/models.py` declares `Base = DeclarativeBase` and one mapped class per table listed in the design plan's Data Plane section:
- `Submission`, `App`, `Asset`, `ScanResult`, `DetectionFinding`, `ApprovalEvent`, `ReviewerToken`, `RevokedToken`, `AccessLog`, `SigningKeyVersion`, `Agent`, `WebhookSubscription`, `CostSnapshotMonthly`, `SharedReferenceCatalog`, `IdempotencyKey`.

`IdempotencyKey` is a **design deviation**: the design does not enumerate this table. It is required because `asgi-idempotency-header`'s in-memory store does not survive across multiple ACA replicas. Postgres-backed store is necessary for AC3.2 to hold in production. This deviation is documented in `docs/implementation-plans/2026-04-23-rac-v1/README.md` under "Approved design deviations."

Conventions (per design):
- `snake_case` column names; `id: Mapped[UUID]` with server-side default `sa.text("uuidv7()")` (Postgres 16 with `pg_uuidv7` extension enabled in Phase 1 Bicep) and client-side fallback via `edwh-uuid7` for test fixtures.
- `created_at: Mapped[datetime]` (`server_default=func.now()`, `timezone=True`), `updated_at: Mapped[datetime]` where applicable.
- All FKs `ondelete="RESTRICT"`.
- JSONB columns only on `submission.manifest`, `scan_result.findings`, `webhook_subscription.filter`, `agent.metadata`.
- `Submission.status` is a Postgres ENUM type (Alembic-created) with all states from the FSM diagram.

`migrations/env.py` uses the async driver path and runs under `asyncio.run`. `target_metadata = Base.metadata`. Naming conventions set (`ix_%(column_0_label)s`, `uq_%(table_name)s_%(column_0_name)s`, etc.) so autogenerate produces deterministic constraint names.

`0001_initial_schema.py`: full schema including `CREATE EXTENSION IF NOT EXISTS pg_uuidv7;`, status ENUM, all 15 tables, all FKs, all indexes (on `submission.submitter_principal_id`, `submission.app_id`, `submission.status`, `approval_event.submission_id`, `access_log.reviewer_token_jti`, `access_log.created_at DESC`, etc.). It also **runs the grant-revoke DDL for append-only tables**:

```sql
-- Example for access_log
REVOKE UPDATE, DELETE ON access_log FROM rac_app;
-- Same for approval_event, revoked_token, detection_finding (insert path)
```

(The `rac_app` role is created by the operator during Tier 1 bootstrap; the migration REVOKEs instead of GRANTs-then-REVOKEs to be idempotent. Document in the migration header that this relies on the role existing; add a pre-flight check that raises a clear error if the role is missing.)

`tests/test_schema_migration.py` (integration — uses testcontainers Postgres):
- Boots a Postgres 16 container with `pg_uuidv7` extension, runs Alembic upgrade head, asserts every expected table exists, every expected index, every expected enum value, that `uuidv7()` is callable, and that `rac_app` role (created in fixture) cannot UPDATE/DELETE `access_log`, `approval_event`, `revoked_token` rows (inserts succeed, mutating statements raise `InsufficientPrivilege`). The skill `writing-good-tests` applies: test the behavior (DB rejects mutation), not the internal grant query.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_schema_migration.py -v
```

**Commit:** `feat(control-plane): full v1 schema + append-only grants`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Interactive OIDC auth (Entra, researcher SSO)

**Verifies:** `rac-v1.AC2.3`, `rac-v1.AC2.6`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/auth/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/auth/entra.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/auth/principal.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_auth_interactive.py`

**Implementation:**

`principal.py` (pure): defines `@dataclass(frozen=True) class Principal` with `oid: UUID`, `kind: Literal['user','agent']`, `display_name: str | None`, `agent_id: UUID | None`, `roles: frozenset[str]`. Pure function `principal_from_claims(claims: dict) -> Principal` maps Entra/claim dicts to `Principal`; raises `AuthError` on missing `oid`/`sub`.

`entra.py`: uses `fastapi-azure-auth`'s `SingleTenantAzureAuthorizationCodeBearer` configured with `app_client_id=settings.idp_api_client_id`, `tenant_id=settings.idp_tenant_id`, `allow_guest_users=False`, `scopes={"api://rac-control-plane/submit": "Submit applications"}`. FastAPI dependency `current_principal()` calls the bearer, then feeds the claims into `principal_from_claims`, and returns a `Principal` for DI consumption.

Missing/invalid token → `fastapi-azure-auth` raises a 401 automatically with `WWW-Authenticate: Bearer` header; this satisfies AC2.3.

`tests/test_auth_interactive.py` (integration with `mock-oidc` testcontainer — fixture in Task 8):
- Valid id-token with `oid` claim → principal constructed; passes.
- Missing token → 401 at a protected route (`/me` test-only route).
- Wrong audience → 401.
- Guest user → 401.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_auth_interactive.py -v
```

**Commit:** `feat(control-plane): interactive OIDC auth (Entra)`
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Client-credentials auth and agent resolution

**Verifies:** `rac-v1.AC3.1`, `rac-v1.AC3.5`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/auth/client_credentials.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/data/agent_repo.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_auth_client_credentials.py`

**Implementation:**

`client_credentials.py`: a second `fastapi-azure-auth` flow (`AzureAuthorizationCodeBearer` configured for client-credentials tokens — these carry `appid`/`app_id` instead of `oid`). A combined `current_principal()` dependency first tries the interactive flow, then falls back to client-credentials flow (both are valid Bearer tokens; auth discriminates by token claims).

For client-credentials tokens:
1. Extract `appid` from validated claims.
2. `agent_repo.get_by_entra_app_id(appid)` looks up the corresponding `agent` row in Postgres.
3. If the agent is `enabled=false` or missing: raise `ForbiddenError` (403).
4. Return a `Principal` with `kind='agent'`, `agent_id=agent.id`, `oid=agent.service_principal_id`.

`agent_repo.py`: async SQLAlchemy repo with `get_by_entra_app_id(app_id: str)` and later `list_agents()`, `create_agent(...)`. Keeps all DB access in the repo (FCIS shell).

`tests/test_auth_client_credentials.py` (integration with mock-oidc client-credentials flow):
- Enabled agent → principal has `kind='agent'` and correct `agent_id`; protected route returns 200.
- Unknown `appid` → 403.
- Enabled=false agent → 403.
- Later (Task 10) submissions created through this path have `agent_id` populated.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_auth_client_credentials.py -v
```

**Commit:** `feat(control-plane): client-credentials auth + agent resolution`
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Submission FSM + slug derivation (Functional Core)

**Verifies:** `rac-v1.AC2.1` (slug + status part), supports `rac-v1.AC2.2` (used by Phase 5)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/submissions/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/submissions/fsm.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/submissions/slug.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_submissions_fsm.py`
- Create: `apps/control-plane/backend/tests/test_submissions_slug.py`

**Implementation:**

`fsm.py` (pure): declare `SubmissionStatus: StrEnum` matching the design diagram (`awaiting_scan`, `pipeline_error`, `scan_rejected`, `needs_user_action`, `needs_assistance`, `awaiting_research_review`, `research_rejected`, `awaiting_it_review`, `it_rejected`, `approved`, `deployed`). Pure function `transition(current: SubmissionStatus, event: TransitionEvent) -> SubmissionStatus` where `TransitionEvent` is a discriminated union of events (`PipelineError`, `SevGateFailed`, `ScanPassed`, `ResearchApproved`, `ResearchRejected`, `ITApproved`, `ITRejected`, `ProvisioningCompleted`, `UserRequestsAssistance`, `UserResolvesActionNeeded`). Invalid transitions raise `InvalidTransitionError`. Property-based tests (Hypothesis) assert the state graph matches the design: no event can reach a terminal state (`deployed`, `it_rejected`, `research_rejected`, `scan_rejected`) from outside the allowed arrow; `deployed` is a sink.

`slug.py` (pure): `def derive_slug(paper_title: str | None, github_repo: str, existing_slugs: Iterable[str]) -> str`. Rules from design: prefer short, human-readable slug derived from paper_title (lowercase, non-alphanumeric→dash, max 40 chars, no leading/trailing dashes); if title missing or collision, fall back to `repo-basename`; if still colliding, append `-N` (smallest N ≥ 2 making it unique). Property-based test: generated slugs always match `^[a-z0-9]+(-[a-z0-9]+)*$`, always ≤ 40 chars, always unique against the given set.

`tests/test_submissions_fsm.py`: parametrized tests for every legal transition in the diagram + rejection tests for illegal ones. Property test: starting from any status, no sequence of events escapes the allowed state space.

`tests/test_submissions_slug.py`: examples + Hypothesis property tests.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_submissions_fsm.py apps/control-plane/backend/tests/test_submissions_slug.py -v
```

Apply the `ed3d-house-style:property-based-testing` skill here — FSM and slug derivation are canonical targets.

**Commit:** `feat(control-plane): submission FSM + slug derivation (pure)`
<!-- END_TASK_7 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 8-11) -->

<!-- START_TASK_8 -->
### Task 8: Test fixtures — Postgres and mock OIDC

**Verifies:** None (test infrastructure)

**Files:**
- Create: `apps/control-plane/backend/tests/fixtures/__init__.py`
- Create: `apps/control-plane/backend/tests/fixtures/db.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/fixtures/oidc.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/fixtures/client.py` (pattern: Imperative Shell)

**Implementation:**

`fixtures/db.py`:
- Session-scoped `postgres_container` fixture: `PostgresContainer("postgres:16-alpine")` with `pg_uuidv7` installed via a custom init SQL file mounted into the container (`/docker-entrypoint-initdb.d/init.sql` → `CREATE EXTENSION IF NOT EXISTS pg_uuidv7; CREATE ROLE rac_app;`).
- Session-scoped `pg_dsn` fixture builds the connection URL.
- Session-scoped `migrated_db` fixture: connect as superuser, run `alembic upgrade head`, yield.
- Function-scoped `db_session` fixture: creates an `AsyncSession`, starts a savepoint, yields, rolls back — keeps tests isolated without re-migrating.

`fixtures/oidc.py`:
- Session-scoped `mock_oidc` fixture runs `mock-oidc` (the Prdp1137 image) via `GenericContainer`, exposes well-known JWKS URL, offers helpers `issue_user_token(oid: UUID, roles: list[str])` and `issue_client_credentials_token(app_id: UUID, scopes: list[str])` returning Bearer strings.

`fixtures/client.py`:
- Function-scoped `app` fixture: builds a `FastAPI` app with test settings (DSN from `pg_dsn`, IdP config pointing at `mock_oidc`), runs the migrator, returns the app.
- Function-scoped `client` fixture: `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.

Settings override mechanism: tests set env vars via `monkeypatch.setenv(...)` then call `get_settings.cache_clear()` before constructing the app.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest -k "fixtures" --collect-only  # fixtures wire up correctly
```

(No logic tests — the fixtures are exercised by every subsequent test.)

**Commit:** `chore(control-plane): pytest fixtures for Postgres + mock OIDC`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: Idempotency-Key middleware (Postgres-backed store)

**Verifies:** `rac-v1.AC3.2`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/middleware/idempotency.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/idempotency.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_idempotency.py`

**Implementation:**

The off-the-shelf `asgi-idempotency-header` library is designed for in-memory/Redis storage; we need Postgres-backed storage for correctness in a multi-replica ACA environment. Implement the middleware ourselves with the same contract:

`services/idempotency.py` (pure): functions `hash_request(method: str, path: str, body_bytes: bytes) -> str` (SHA256 of method + path + body), `validate_key(key: str) -> bool` (UUID or ≤ 256 char string per RFC 9457 draft). Decides "is this a duplicate" by comparing stored hash vs new request hash; pure logic.

`api/middleware/idempotency.py` (shell): ASGI middleware for mutating methods (`POST`, `PUT`, `DELETE`, `PATCH`). On request:
1. Read `Idempotency-Key` header; if absent, pass through.
2. Compute request-hash via `hash_request`.
3. `INSERT ... ON CONFLICT DO NOTHING` into `idempotency_key` table keyed on `(key, principal_id)` storing `(request_hash, response_status, response_body, response_headers, created_at)`.
4. If insert succeeded (new key), run the downstream handler, capture the response, `UPDATE` the row with the response, return it.
5. If insert conflicted (existing key): fetch the stored row. If stored `request_hash == new_hash`, return the stored response verbatim (AC3.2: same `submission_id`, 200, not a new row). If hashes differ, return 422 "Idempotency-Key reused with different request body". TTL = 24 h; expired rows purged by a periodic job (Phase 5-adjacent or lazy expiry here).

`tests/test_idempotency.py` (integration):
- Two `POST /submissions` with same key + same body: first returns 200 with `submission_id=X`; second returns 200 with `submission_id=X` and no new DB row created.
- Same key + different body: second returns 422.
- No key: two requests create two rows (current behavior — caller opted out).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_idempotency.py -v
```

**Commit:** `feat(control-plane): Idempotency-Key middleware (Postgres-backed)`
<!-- END_TASK_9 -->

<!-- START_TASK_10 -->
### Task 10: Submission CRUD API (POST, GET, LIST)

**Verifies:** `rac-v1.AC2.1`, `rac-v1.AC2.3`, `rac-v1.AC2.4`, `rac-v1.AC2.6`, `rac-v1.AC3.1`, `rac-v1.AC3.2`, `rac-v1.AC3.5`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/schemas/submissions.py` (type-only, no FCIS tag)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py` (pattern: Functional Core where possible; thin Shell wrapper for DB writes)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/github_validation.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/data/submission_repo.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_submissions_api.py`

**Implementation:**

`schemas/submissions.py` — Pydantic request/response models:
- `SubmissionCreateRequest`: `github_repo_url: HttpUrl`, `git_ref: str = "main"`, `dockerfile_path: str = "Dockerfile"`, `paper_title: str | None`, `pi_principal_id: UUID`, `dept_fallback: str`, `manifest: dict | None` (raw parsed `rac.yaml` — optional in v1 for UI users; Phase 8 validates).
- `SubmissionResponse`: `id: UUID`, `slug: str`, `status: SubmissionStatus`, `submitter_principal_id: UUID`, `agent_id: UUID | None`, `github_repo_url: HttpUrl`, `git_ref: str`, `dockerfile_path: str`, `pi_principal_id: UUID`, `dept_fallback: str`, `created_at: datetime`, `updated_at: datetime`, `manifest: dict | None`.
- `SubmissionListResponse`: pagination envelope.

`services/github_validation.py`: `async def validate_repo(url: HttpUrl, ref: str, dockerfile_path: str) -> None`. Uses `httpx.AsyncClient` with a short timeout (5s). Calls GitHub's REST API (unauthenticated — rate-limited; allow optional PAT from settings) to HEAD the repo, then `GET /repos/:owner/:name/contents/:path?ref=:ref` to confirm the Dockerfile exists. Raises `ValidationApiError("github_not_found", "Repository or ref not found: {url}@{ref}")` on 404. Raises `ValidationApiError("dockerfile_not_found", ...)` if Dockerfile missing at path.

`services/submissions/create.py`: `async def create_submission(session, principal, req, existing_slugs)`:
1. Pure: call `slug.derive_slug(req.paper_title, str(req.github_repo_url), existing_slugs)`.
2. Impure: call `github_validation.validate_repo(...)` (AC2.4 — raises before DB write).
3. Pure: construct `Submission` ORM instance with `status=awaiting_scan`, `submitter_principal_id=principal.oid`, `agent_id=principal.agent_id` (None for interactive flow — AC2.6 + AC3.1), `slug=<derived>`, all other fields from request.
4. Impure: `session.add(submission); await session.flush()` to get DB-generated UUIDv7.
5. Impure: insert `approval_event` row with `kind='submission_created'`, `actor_principal_id=principal.oid`, `submission_id=submission.id`.
6. Return ORM object.

`data/submission_repo.py`: `async def get_by_id(session, id) -> Submission | None`, `async def list_submissions(session, *, principal, page, size, status_filter) -> tuple[list[Submission], int]`, `async def get_existing_slugs(session) -> set[str]`.

`api/routes/submissions.py`:
- `POST /submissions`: depends on `current_principal` (combined auth — interactive OR client credentials), reads body, calls service, returns 201 with `SubmissionResponse`.
- `GET /submissions/{id}`: auth required; returns 404 if not found; authorized only if principal is submitter, approver with appropriate role, or admin (role check via principal.roles).
- `GET /submissions`: listing with pagination, status filter.

`tests/test_submissions_api.py` (integration — every case):
- AC2.1: interactive user creates → row exists with `status=awaiting_scan`, `submitter_principal_id=user.oid`, slug derived from `paper_title`.
- AC2.3: no auth → 401 with `WWW-Authenticate: Bearer`.
- AC2.4: `github_repo_url` returns 404 at GitHub → response is 422 `github_not_found`, no submission row created. Use `respx` to mock httpx calls.
- AC2.6: created by user → `submitter_principal_id` equals the user's `oid` in every related table (`submission`, `approval_event`).
- AC3.1: agent submits via client-credentials → row has `agent_id=<the agent>` and `submitter_principal_id=<the service principal oid>`.
- AC3.2: same `Idempotency-Key` twice → one row, two identical responses (asserted via `X-Idempotent-Replay: true` response header added by the middleware on replay).
- AC3.5: disabled agent → 403, no row.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_submissions_api.py -v
```

**Commit:** `feat(control-plane): submission CRUD (POST, GET, LIST) with full auth coverage`
<!-- END_TASK_10 -->

<!-- START_TASK_11 -->
### Task 11: Agent management endpoints (admin-only)

**Verifies:** Supports `rac-v1.AC3.1`, `rac-v1.AC3.5`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/agents.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/schemas/agents.py`
- Create: `apps/control-plane/backend/tests/test_agents_api.py`

**Implementation:**

Endpoints (all require `principal.roles` including admin role — `settings.approver_role_it` as stand-in for v1; formalized later):
- `POST /agents`: create agent. Body: `name`, `kind` ('ui'|'servicenow'|'cli'|'other'), `entra_app_id: UUID`, `metadata: dict = {}`, `enabled: bool = true`.
- `GET /agents`: list.
- `GET /agents/{id}`: detail.
- `PATCH /agents/{id}`: update `enabled`, `metadata`, `name`.

These endpoints allow operators to register/disable agents. The `web-ui` agent row representing the Control Plane's own frontend is inserted by Alembic in a data migration (`0002_seed_web_ui_agent.py`) so the UI flow can still be attributed to an agent when that path is used.

Tests:
- Admin creates agent → row exists.
- Non-admin POST → 403.
- Disable agent, retry earlier client-credentials test → now returns 403 (AC3.5 parity).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_agents_api.py -v
```

**Commit:** `feat(control-plane): agent management endpoints + web-ui seed`
<!-- END_TASK_11 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_SUBCOMPONENT_D (tasks 12-13) -->

<!-- START_TASK_12 -->
### Task 12: React frontend — MSAL auth, submission form, submission list

**Verifies:** `rac-v1.AC2.1` (via UI)

**Files:**
- Create: `apps/control-plane/frontend/src/lib/msal.ts` (pattern: Imperative Shell)
- Create: `apps/control-plane/frontend/src/lib/api.ts` (pattern: Imperative Shell)
- Create: `apps/control-plane/frontend/src/routes/__root.tsx`
- Create: `apps/control-plane/frontend/src/routes/index.tsx`
- Create: `apps/control-plane/frontend/src/routes/submissions/index.tsx`
- Create: `apps/control-plane/frontend/src/routes/submissions/new.tsx`
- Create: `apps/control-plane/frontend/src/features/submissions/new-submission-form.tsx` (pattern: Functional Core — pure render + Zod schema; form submit is the shell boundary)
- Create: `apps/control-plane/frontend/src/features/submissions/schemas.ts` (type-only, no FCIS tag)
- Create: `apps/control-plane/frontend/src/features/submissions/submissions-list.tsx`
- Create: `apps/control-plane/frontend/src/tests/new-submission-form.test.tsx`

**Implementation:**

`lib/msal.ts` configures `PublicClientApplication` from `@azure/msal-browser` with `authority: https://login.microsoftonline.com/${VITE_TENANT_ID}`, `clientId: VITE_FRONTEND_CLIENT_ID`, `redirectUri: window.location.origin`. Exports the instance and an `acquireTokenSilent` helper targeting the API scope `api://rac-control-plane/submit`.

`lib/api.ts` is the shell boundary to the FastAPI backend. Wraps `fetch` with:
- Injects `Authorization: Bearer <token>` from `acquireTokenSilent`.
- On 401, triggers `acquireTokenRedirect`.
- Adds `Idempotency-Key: crypto.randomUUID()` on mutating requests.
- Adds `X-Request-Id: crypto.randomUUID()` for correlation (echo-back validated in integration tests).
- Returns typed responses via `zod` schema parsing.

Routes use TanStack Router file-based routing; root route wraps app in `MsalProvider` + `QueryClientProvider`; `/submissions/new` is an authenticated route (redirects to login if no account).

Form: React Hook Form + Zod schema (`githubRepoUrl: z.string().url()`, `gitRef: z.string().default('main')`, `dockerfilePath: z.string().default('Dockerfile')`, `paperTitle: z.string().optional()`, `piPrincipalId: z.string().uuid()`, `deptFallback: z.string().min(1)`). Submit calls `POST /submissions`, redirects to `/submissions/{id}` on success, renders per-field errors from backend `ValidationApiError` payloads.

`features/submissions/submissions-list.tsx` uses TanStack Query `useQuery(['submissions', page, statusFilter], api.listSubmissions)` with pagination and status filter dropdown.

Vitest unit test (`new-submission-form.test.tsx`): renders form with mock MSAL account, fills fields, mocks `api.createSubmission` via MSW (`msw` is a dev dep) — asserts the mutation request body matches the schema, and on 422 response, per-field errors render.

**Verification:**
```bash
cd /home/sysop/rac/apps/control-plane/frontend
pnpm typecheck
pnpm build
pnpm test  # vitest
```

**Commit:** `feat(control-plane): React MSAL + submission form + list`
<!-- END_TASK_12 -->

<!-- START_TASK_13 -->
### Task 13: Dockerfile + end-to-end local dev compose

**Verifies:** None (operational)

**Files:**
- Create: `apps/control-plane/Dockerfile`
- Create: `apps/control-plane/docker-compose.dev.yml`
- Create: `apps/control-plane/.env.example`
- Create: `apps/control-plane/Makefile`

**Implementation:**

Multi-stage Dockerfile:
1. `FROM node:20-alpine AS fe-builder`: `pnpm install --frozen-lockfile`, `pnpm build`. Output at `/app/frontend/dist`.
2. `FROM python:3.12-slim AS be-builder`: `uv sync --frozen --no-dev`. Output at `/app/.venv`.
3. `FROM python:3.12-slim AS runtime`: copy venv, copy `src/`, copy `frontend/dist/` into `/app/static/`, set `PYTHONPATH`, healthcheck, `CMD ["gunicorn", "rac_control_plane.main:app", "--worker-class", "uvicorn.workers.UvicornWorker", "--workers", "4", "--bind", "0.0.0.0:8080"]`. Non-root user (`USER 10001`).

`docker-compose.dev.yml`: three services for local end-to-end testing:
- `postgres`: `postgres:16-alpine` with init SQL to install `pg_uuidv7` + create `rac_app` role
- `mock-oidc`: Prdp1137 mock-oidc image
- `control-plane`: built from Dockerfile, env vars pointing at postgres + mock-oidc

`.env.example`: full list of required env vars (matches `settings.py`), with safe example values and comments.

`Makefile` (optional but valuable) with `make dev`, `make test`, `make lint`, `make migrate`, `make build`.

**Verification:**
```bash
cd /home/sysop/rac/apps/control-plane
docker compose -f docker-compose.dev.yml up -d postgres mock-oidc
docker build -t rac-control-plane:dev .
docker run --rm --network rac-control-plane_default --env-file .env.dev rac-control-plane:dev python -c "import rac_control_plane.main"  # imports cleanly
docker compose down
```

**Commit:** `feat(control-plane): Dockerfile + dev compose`
<!-- END_TASK_13 -->

<!-- END_SUBCOMPONENT_D -->

<!-- START_SUBCOMPONENT_E (tasks 13B-13C) -->

<!-- START_TASK_13B -->
### Task 13B: OpenTelemetry metric setup + Control Plane metric emitters (Imperative Shell)

**Verifies:** `rac-v1.AC10.2` (partial — submission and approval metrics)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/metrics.py` (pattern: Imperative Shell)

**Implementation:**

`metrics.py` initialises the OpenTelemetry SDK and declares the two Control Plane instruments:

```python
# pattern: Imperative Shell
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

def configure_metrics(otlp_endpoint: str) -> None:
    """Call once at application startup after settings are loaded."""
    exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=30_000)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)

_meter = metrics.get_meter("rac.control_plane")

submission_counter = _meter.create_counter(
    name="rac.submissions.by_status",
    description="Count of submission FSM state transitions, labeled by target status.",
    unit="1",
)

approval_duration_histogram = _meter.create_histogram(
    name="rac.approvals.time_to_decision_seconds",
    description="Wall-clock seconds from submission creation to first approval decision.",
    unit="s",
)
```

Call `configure_metrics(settings.otlp_endpoint)` in `main.py`'s lifespan startup block after settings are loaded (add `otlp_endpoint: str` to `settings.py`, defaulting to `"http://localhost:4317"` so tests don't need a real endpoint).

Emit `submission_counter.add(1, {"status": new_status})` from the FSM transition helpers in `services/submissions/fsm.py` (add an `emit_metric` callback parameter — the FSM stays Functional Core by accepting the callback rather than importing the counter). Pass `metrics.submission_counter.add` from the Shell callers.

Emit `approval_duration_histogram.record(elapsed_seconds, {"decision": decision_type})` from the approval service in Phase 5 Task 4. The call site is in `services/approvals/record.py`; do NOT add a TODO comment in Phase 2 code — the histogram is declared here and wired in Phase 5.

Dependencies to add to `apps/control-plane/backend/pyproject.toml`:
- `opentelemetry-sdk`
- `opentelemetry-exporter-otlp-proto-grpc`

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_metrics.py -v
```

**Commit:** `feat(control-plane): OTel metrics setup + submission counter (AC10.2 partial)`
<!-- END_TASK_13B -->

<!-- START_TASK_13C -->
### Task 13C: Metric emitter unit tests

**Verifies:** `rac-v1.AC10.2` (partial — validates counter increments on FSM transitions)

**Files:**
- Create: `apps/control-plane/backend/tests/test_metrics.py`

**Implementation:**

Use the OTel in-memory `InMemoryMetricReader` (no OTLP server needed):

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from rac_control_plane.metrics import _meter, submission_counter

def test_submission_counter_increments_on_fsm_transition():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # Replace global meter (test isolation)
    metrics.set_meter_provider(provider)
    counter = provider.get_meter("rac.control_plane").create_counter("rac.submissions.by_status")

    counter.add(1, {"status": "awaiting_scan"})
    counter.add(1, {"status": "scan_complete"})

    data = reader.get_metrics_data()
    points = [p for m in data.resource_metrics for sm in m.scope_metrics
              for metric in sm.metrics for p in metric.data.data_points]
    statuses = {p.attributes["status"]: p.value for p in points}
    assert statuses["awaiting_scan"] == 1
    assert statuses["scan_complete"] == 1
```

Also add an integration test that a real `POST /submissions` call (against testcontainers + mock-oidc) results in a metric data point with `status=awaiting_scan` being recorded.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_metrics.py -v
```

**Commit:** `test(control-plane): metric emitter unit tests (AC10.2)`
<!-- END_TASK_13C -->

<!-- END_SUBCOMPONENT_E -->

<!-- START_TASK_14 -->
### Task 14: End-to-end acceptance pass on AC2/AC3/AC12 subset

**Verifies:** All Phase 2 ACs (meta-verification)

**Files:** None (verification task)

**Implementation:**

Run the full Control Plane test suite against a fresh testcontainers Postgres + mock-oidc. Record the outcome in scratchpad.

Manual smoke test:
1. `docker compose -f docker-compose.dev.yml up -d`
2. Open browser to `http://localhost:8080/` (served by FastAPI static mount).
3. Log in via mock-oidc redirect (test identity).
4. Submit the form with a valid GitHub URL (use a public repo, e.g., `https://github.com/anchore/grype` with `Dockerfile` at root).
5. Confirm submission list shows the new row with `status=awaiting_scan`.
6. `curl -H "Authorization: Bearer <client_credentials_token>" -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" -d @submission.json http://localhost:8080/submissions` returns 201 with `agent_id` populated.
7. Re-run same curl with same idempotency key → identical response, one row in DB.
8. Disable the agent via `PATCH /agents/{id}` → retry → 403.

Write findings to scratchpad as `phase2-acceptance-report.md`.

**Verification:** Commands above. All ACs listed in the Coverage section must pass.

**Commit:** None (verification only).
<!-- END_TASK_14 -->

---

## Phase 2 Done Checklist

- [ ] All unit + integration tests pass (`uv run --project apps/control-plane/backend pytest`)
- [ ] Frontend builds and typechecks (`pnpm build && pnpm typecheck && pnpm test`)
- [ ] Dockerfile builds; container imports cleanly and serves `/health`
- [ ] ACs from Coverage section all verified via test cases and smoke test
- [ ] No file with runtime behavior is unclassified for FCIS
- [ ] No plaintext secrets in source; `.env.example` is safe to commit
- [ ] Acceptance report saved to scratchpad
