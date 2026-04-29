# Pipeline Trust Implementation Plan — Phase 3: Control Plane

**Goal:** Eliminate the placeholder-callback-secret bug in the original create-submission dispatch path; consolidate dispatch logic from `services/submissions/create.py` AND `services/pipeline_dispatch/retry.py` into a new shared helper `services/pipeline_dispatch/dispatch_helper.py`; replace the silent-no-op behavior in `_build_dispatch_fn` (when `RAC_GH_PAT` is unset) with a `DispatchUnavailableError` → 503 response; add a dedicated `pipeline_kv_uri` Settings field plumbed via `RAC_PIPELINE_KV_URI` env var on the control-plane container app.

**Architecture:** A new Imperative Shell module `dispatch_helper.py` exposes one function — `dispatch_for_submission(submission, *, settings, triggered_by)` — that mints the callback secret in the dedicated pipeline KV (`settings.pipeline_kv_uri`), builds the payload via the existing `build_dispatch_payload` (pure), and POSTs `repository_dispatch` via `github.dispatch`. Both `create.py` and `retry.py` delegate to this helper. Missing `gh_pat` OR missing `pipeline_kv_uri` raises `DispatchUnavailableError`, which the API layer maps to a 503 with idempotency-replay support. The placeholder-secret string in `create.py:200` is removed; the silent-no-op in `_build_dispatch_fn` (`submissions.py:94-96`) is removed; the create-submission route catches `DispatchUnavailableError` mirroring `provisioning.py:309-310`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, structlog, pytest + pytest-asyncio, real Postgres test container, mocked Azure KV `SecretClient` and mocked `gh_dispatch.dispatch`.

**Scope:** Phase 3 of 5. Pure control-plane code refactor + tests + a single bicep env-var line. No infra deploy in this phase (Phase 5 deploys Pass 2). No Python schema changes; no DB migration.

**Codebase verified:** 2026-04-28 by codebase-investigator.

**Verification snapshot (key files + line refs):**
- ✓ Placeholder bug location: `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py:185-228`. The lines `secret_name_placeholder = f"rac-pipeline-cb-{submission.id}"` (line 200) → `callback_secret_name=secret_name_placeholder` (line 204) is the bug. The comment on lines 196-198 even *says* the real secret should be minted by the caller — but no caller mints it. We replace this whole `if dispatch_fn is not None…` block with a single `await dispatch_for_submission(...)` call.
- ✓ Existing `retry.py` (`services/pipeline_dispatch/retry.py`, 93 lines) carries the full reference dispatch sequence: gh_pat check (lines 56-60) → `mint_callback_secret` (63-67) → `build_dispatch_payload` (69-73) → audit log (75-79) → `gh_dispatch.dispatch` (81-86) → return dict (88-91). It also defines `DispatchUnavailableError` (line 29). All of this lifts into `dispatch_helper.py` essentially unchanged; `retry.py` then becomes a thin wrapper that adds the `admin_oid` audit-log enrichment and delegates.
- ✓ Silent-no-op location: `api/routes/submissions.py:79-104`. Lines 94-96 are the silent skip:
  ```python
  if not auth_token:
      logger.warning("pipeline_dispatch_skipped_no_auth_token")
      return None
  ```
  We remove the entire `_build_dispatch_fn` (it's no longer needed once create.py routes through `dispatch_for_submission`, which gets settings directly).
- ✓ `_build_dispatch_fn` is called once at `submissions.py:145` (`dispatch_fn = _build_dispatch_fn(settings)`) and the result is passed as `dispatch_fn=dispatch_fn` into `create_submission` at line 179. Both go away.
- ✓ Existing 503 mapping is `ServiceUnavailableError(public_message=…)` at `api/routes/provisioning.py:309-310`. Mirror the import + try/except in `submissions.py`.
- ✓ Settings class lives at `apps/control-plane/backend/src/rac_control_plane/settings.py:9-107`; `kv_uri: str` at line 32 is the existing platform-KV field; `model_config = {"env_prefix": "RAC_"}` at line 91 means `pipeline_kv_uri` automatically maps to `RAC_PIPELINE_KV_URI`.
- ✓ Existing GH-related env vars on the control-plane ACA app live in `infra/modules/control-plane-aca-app.bicep:175-205`. `RAC_KV_URI` is set at ~line 187; we insert `RAC_PIPELINE_KV_URI` after it, sourced from a new `pipelineKvUri` module input (default `''`).
- ✓ `mint_callback_secret` (`services/pipeline_dispatch/secret_mint.py:19-77`) signature: `(submission_id, *, kv_uri, expiry_minutes, client=None) -> (secret_name, secret_value)`. Already accepts `kv_uri` as a parameter — no change required to its signature, just pass `settings.pipeline_kv_uri` instead of `settings.kv_uri` at the call sites.
- ✓ Idempotency middleware records non-2xx responses: `api/middleware/idempotency.py:225-227` confirms `record.response_status = response.status_code` runs unconditionally. AC6.4 (replay 503 on same key) is therefore structurally satisfied; we just need a test that exercises it.
- ✓ Existing test files: `tests/test_create_submission.py` (324 lines), `tests/test_dispatch_retry_api.py` (155 lines, the recent addition), `tests/test_pipeline_dispatch.py` (165 lines). The retry test file demonstrates the mocking pattern (`mint_callback_secret`, `gh_dispatch.dispatch`) we mirror for new tests.
- ✓ Audit log line in retry: `logger.info("pipeline_dispatch_retry", submission_id=..., admin_oid=...)`. Per design, the helper takes `triggered_by: 'submission_created' | 'admin_retry'`; both retry.py and create.py emit a single `pipeline_dispatched` log line tagged with `triggered_by`. The retry.py wrapper additionally emits `admin_oid` after calling the helper, preserving the existing audit shape.

**External research findings:** None new. All work is internal Python + one bicep env-var line.

---

## Acceptance Criteria Coverage

### pipeline-trust.AC5: `create.py` placeholder-secret bug fixed
- **pipeline-trust.AC5.1 Success:** integration test `tests/test_submissions_api.py::test_create_submission_happy_path_dispatches_with_real_secret_name` asserts the dispatched payload contains `callback_secret_name == f"rac-pipeline-cb-{submission_id}"` (matching format from `mint_callback_secret`).
- **pipeline-trust.AC5.2 Success:** `grep -r "PLACEHOLDER" apps/control-plane/backend/src/rac_control_plane/services/` returns no results in dispatch-related code paths.
- **pipeline-trust.AC5.3 Success:** both `create.py` and `retry.py` route through `dispatch_for_submission` (verified by import-graph test or grep).
- **pipeline-trust.AC5.4 Failure:** if the helper is bypassed (regression), `test_create_submission_happy_path_dispatches_with_real_secret_name` fails loudly.

### pipeline-trust.AC6: Loud-fail on missing PAT
- **pipeline-trust.AC6.1 Success:** with `RAC_GH_PAT` unset, `POST /api/submissions` returns HTTP 503 with body `{"code": "service_unavailable", "message": "...RAC_GH_PAT...", "correlation_id": "..."}`.
- **pipeline-trust.AC6.2 Failure:** the 503 path does NOT insert a row into the `submission` table (`tests/test_submissions_api.py::test_create_submission_no_orphan_row_on_dispatch_503`).
- **pipeline-trust.AC6.3 Failure:** the 503 path does NOT create a secret in the pipeline KV (no orphan secret).
- **pipeline-trust.AC6.4 Edge:** idempotency middleware records the 503 response so a replay of the same `Idempotency-Key` returns the same 503 (no double-execution).
- **pipeline-trust.AC6.5 Failure:** the silent-no-op branch in `_build_dispatch_fn` is gone; a grep for `pipeline_dispatch_skipped_no_auth_token` log line returns zero hits.

**Verifies in tests:** every AC.N case above maps to a named test in `tests/test_submissions_api.py` or `tests/test_pipeline_dispatch.py`. Tests exercise real Postgres + mocked KV/HTTP per the project's testing pattern (`apps/control-plane/backend/CLAUDE.md` Tests section).

---

## Notes for the Implementor

- **Read `apps/control-plane/backend/CLAUDE.md` before starting.** Particularly the FCIS pattern markers, the DB-role note (the deployed CP is currently `rac_admin`), the idempotency-middleware contract, the structlog logger-name gotcha, and the test fixtures section.
- **Activate skills:** `ed3d-house-style:howto-functional-vs-imperative` (mandatory for adding new files — every new module declares `# pattern: Imperative Shell` or `# pattern: Functional Core` on line 1), `ed3d-house-style:writing-good-tests`, `ed3d-plan-and-execute:test-driven-development` (write the failing test first; this is functionality, not infra), `ed3d-house-style:howto-develop-with-postgres` (tests use real PG).
- **Strict pipeline-KV separation.** When `pipeline_kv_uri` is empty in Settings, the helper raises `DispatchUnavailableError`. Do NOT fall back to `settings.kv_uri` — falling back to the platform KV defeats the AC2.2 isolation guarantee (pipeline RBAC must never reach the platform KV). The user's recorded preference: prefer correctness over leniency. The 503 message should be specific: "Pipeline dispatch is not configured: RAC_PIPELINE_KV_URI is unset."
- **Helper is Imperative Shell.** It takes `settings: Settings` (typed import), calls KV/HTTP. Pure logic (`build_dispatch_payload`) stays untouched in `payload.py`.
- **Preserve the `admin_oid` audit log on retry path.** The existing log line `logger.info("pipeline_dispatch_retry", submission_id=..., admin_oid=...)` must continue to emit when `triggered_by='admin_retry'`. The cleanest split: helper emits a generic `pipeline_dispatched` log; retry.py emits an additional `pipeline_dispatch_retry_admin` line tagged with `admin_oid` before calling the helper.
- **Do NOT break the existing payload-too-large 422 path.** `services/submissions/create.py` lines 209-219 catch `ValidationApiError` from dispatch and mark the submission `pipeline_error` then re-raise. Preserve this behavior. After Phase 3, the dispatch call site becomes `await dispatch_for_submission(...)`; the `except ValidationApiError` block stays intact.
- **DO NOT alter `mint_callback_secret`'s signature.** It already takes `kv_uri` as a parameter; just pass `settings.pipeline_kv_uri` at the new call sites. The expiry remains `pipeline_timeout_minutes * 2`.
- **Remove the `dispatch_fn` parameter from `create_submission()` cleanly — do NOT preserve a test-only branch in production code.** The existing `test_create_submission.py` (324 lines) injects `dispatch_fn` heavily, so this refactor has real blast radius — but the user's recorded preference ("correctness over sloppiness") rules out keeping a branch in production whose only purpose is test convenience. The migration:
  1. `create_submission` no longer takes `dispatch_fn`. It takes `settings: Settings` (typed; passed by the route layer, no `get_settings()` mid-function).
  2. The dispatch call inside `create_submission` is unconditionally `await dispatch_for_submission(submission, settings=settings, triggered_by="submission_created")`.
  3. Tests in `test_create_submission.py` switch from "inject a mock `dispatch_fn`" to "monkeypatch `rac_control_plane.services.submissions.create.dispatch_for_submission`" (the function-import-site monkeypatch pattern). This is a one-line change per test and matches how the new `test_dispatch_helper.py` mocks already work.
  4. The route layer (`api/routes/submissions.py`) passes `settings=get_settings()` into `create_submission`. Production no longer reaches for `get_settings()` mid-function.
- **Why threaded settings, not `get_settings()` inside `create_submission`?** The codebase favors dependency injection per the FCIS discipline (see `apps/control-plane/backend/CLAUDE.md` invariants section). Threading settings as a typed parameter makes the dependency explicit; reaching for `get_settings()` ad-hoc breaks the boundary.

---

<!-- START_TASK_1 -->
### Task 1: Add `pipeline_kv_uri` to Settings

**Verifies:** preparation step (no AC directly; required by Tasks 2-5).

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/settings.py:9-107`
- Test: `apps/control-plane/backend/tests/test_settings.py` (verify file exists; if not, create alongside this task)

**Implementation:**

Add a new field to the `Settings` class adjacent to `kv_uri` (line 32):

```python
pipeline_kv_uri: str = ""
```

Default empty string is intentional: in environments where the pipeline KV has not yet been deployed, the field is empty and dispatch attempts surface the 503 loud-fail. In dev/staging/prod where the bicep has been deployed with `deployPipelineKv=true`, the env var `RAC_PIPELINE_KV_URI` is populated and the field is non-empty.

Pydantic env mapping is automatic via `model_config["env_prefix"] = "RAC_"`.

**Testing:**

Add (or extend if file exists) `apps/control-plane/backend/tests/test_settings.py` with one test:
- `test_pipeline_kv_uri_defaults_to_empty_string` — instantiate `Settings()` with all required env vars set EXCEPT `RAC_PIPELINE_KV_URI`; assert `settings.pipeline_kv_uri == ""`.
- `test_pipeline_kv_uri_loaded_from_env` — set `RAC_PIPELINE_KV_URI=https://kv-rac-pl-foo-dev.vault.azure.net/`; assert it loads.

Test patterns: see existing `tests/test_settings.py` if it exists; otherwise use `monkeypatch.setenv` + `Settings()` direct instantiation. The Settings class is global state via `get_settings()` LRU cache — clear or bypass the cache (`get_settings.cache_clear()` if used) so tests don't pollute each other.

**Verification:**

```bash
cd /home/sysop/rac/apps/control-plane/backend
uv run pytest tests/test_settings.py -v
# Expected: both new tests pass.

uv run mypy src/rac_control_plane/settings.py
# Expected: zero type errors.
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/src/rac_control_plane/settings.py apps/control-plane/backend/tests/test_settings.py
git commit -m "control-plane(settings): add pipeline_kv_uri (RAC_PIPELINE_KV_URI)

The dispatch flow uses a dedicated per-env pipeline KV
(kv-rac-pl-...) for per-submission HMAC callback secrets,
keeping the pipeline identity's RBAC blast radius separate from the
platform KV. This field is the URI of that vault.

Default empty for environments where the pipeline KV has not yet been
provisioned (Pass-1 deploys); the dispatch helper raises
DispatchUnavailableError -> 503 if the field is empty when dispatch
is attempted, surfacing misconfiguration loudly."
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Add `RAC_PIPELINE_KV_URI` env var to the control-plane container app

**Verifies:** preparation step (no AC directly; required for Phase 5 live deploy to pick up the new field).

**Files:**
- Modify: `infra/modules/control-plane-aca-app.bicep` (env block at lines 175-205, plus param block at top of file)

**Implementation:**

1. Add a new bicep parameter near the top of `control-plane-aca-app.bicep` (after the existing `kvUri` param):

```bicep
@description('URI of the dedicated pipeline KV (kv-rac-pl-...). Empty when the pipeline KV is not yet deployed; the control plane raises a loud 503 on dispatch attempts in that case.')
param pipelineKvUri string = ''
```

2. In the env-var block (the `concat([{...}, {...}], …)` array around line 175), insert a new entry after `RAC_KV_URI`:

```bicep
{ name: 'RAC_PIPELINE_KV_URI', value: pipelineKvUri }
```

3. In `infra/main.bicep`, in the `controlPlaneAcaApp` module invocation (lines 522-568), add a new param at the bottom of the params block:

```bicep
    pipelineKvUri: pipelineKv.?outputs.kvUri ?? ''
```

This ties the env var to Phase 2's `pipelineKv` module output — when `deployPipelineKv=false`, the safe-navigation operator returns `''` and the env var is set to empty string. When the gate is on, the env var carries the real vault URI.

**Verification:**

```bash
cd /home/sysop/rac
az bicep build --file infra/modules/control-plane-aca-app.bicep
az bicep build --file infra/main.bicep
# Expected: zero output, exit 0 from each.

# What-if shows zero diff vs live dev (gate still off)
az deployment sub what-if \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  | grep -E "Resource changes|no change" | head -5
# Expected: "no change" (because the new env var threads `''` through).
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/modules/control-plane-aca-app.bicep infra/modules/control-plane-aca-app.json infra/main.bicep infra/main.json
git commit -m "infra(control-plane-aca-app): set RAC_PIPELINE_KV_URI env var

Sourced from pipelineKv.?outputs.kvUri (Phase 2 module output).
Empty when deployPipelineKv=false so what-if remains zero-diff on
ungated deploys; non-empty once Pass 2 lands the pipeline KV."
```
<!-- END_TASK_2 -->

<!-- START_SUBCOMPONENT_A (tasks 3-4) -->

<!-- START_TASK_3 -->
### Task 3: Write failing tests for `dispatch_for_submission`

**Verifies:** pipeline-trust.AC5.1 (real secret name in payload), pipeline-trust.AC5.4 (regression detection), pipeline-trust.AC6.1 (503 on missing PAT — at the helper level, before the route exposure).

**Files:**
- Create: `apps/control-plane/backend/tests/test_dispatch_helper.py`

**Implementation:**

Write tests that lock in the helper's contract BEFORE the helper exists (TDD).

Tests to write (each describes WHAT the helper must do; the implementor writes test code at execution time using project patterns):

1. `test_dispatch_for_submission_mints_secret_in_pipeline_kv_then_dispatches`
   - Setup: stub `mint_callback_secret` (mock the inner `SecretClient` injected via the optional `client=` param), stub `gh_dispatch.dispatch` to capture the payload.
   - Assert: `mint_callback_secret` was called with `kv_uri=<pipeline-kv-uri>` (NOT the platform KV uri); the dispatched payload's `callback_secret_name` is `f"rac-pipeline-cb-{submission.id}"`; the helper returns a dict with keys `submission_id`, `callback_url`, `dispatched_at`.

2. `test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset`
   - Setup: Settings with `gh_pat = None`, `pipeline_kv_uri='https://stub'`.
   - Assert: calling `dispatch_for_submission` raises `DispatchUnavailableError` with `'RAC_GH_PAT'` in the message; `mint_callback_secret` and `gh_dispatch.dispatch` are NOT called (no orphan secret, no GH API call).

3. `test_dispatch_for_submission_raises_DispatchUnavailableError_when_pipeline_kv_uri_empty`
   - Setup: Settings with `gh_pat = SecretStr("ghp_...")`, `pipeline_kv_uri=''`.
   - Assert: raises `DispatchUnavailableError` with `'RAC_PIPELINE_KV_URI'` in the message; `mint_callback_secret` and `gh_dispatch.dispatch` are NOT called.

4. `test_dispatch_for_submission_emits_pipeline_dispatched_log_with_triggered_by`
   - Setup: capture structlog with the project's existing pattern (e.g., `caplog` + structlog test config; mirror retry tests).
   - Assert: a `pipeline_dispatched` log line is emitted with `submission_id=<uuid>` and `triggered_by='submission_created'` when called with that value; with `triggered_by='admin_retry'` the same log line shows that triggered_by value.

Mocking: follow the patterns in `tests/test_dispatch_retry_api.py` and `tests/test_pipeline_dispatch.py`. Use `unittest.mock.patch` or `monkeypatch.setattr` to stub:
- `rac_control_plane.services.pipeline_dispatch.dispatch_helper.mint_callback_secret`
- `rac_control_plane.services.pipeline_dispatch.dispatch_helper.gh_dispatch.dispatch` (or the import path actually used)

Submission ORM object: use a real Submission via the existing `db_session` / `migrated_db` fixtures from `tests/fixtures/db.py`, OR a lightweight in-memory mock if the helper only reads `submission.id` and `submission.github_repo_url`. Inspect existing retry tests to see which pattern is preferred.

**Verification:**

```bash
cd /home/sysop/rac/apps/control-plane/backend
uv run pytest tests/test_dispatch_helper.py -v
# Expected: ALL FOUR TESTS FAIL (red phase of TDD; helper does not exist yet).
# The failure messages should be ImportError or ModuleNotFoundError on
# `rac_control_plane.services.pipeline_dispatch.dispatch_helper`.
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/tests/test_dispatch_helper.py
git commit -m "test(dispatch-helper): failing tests pin contract for new helper

Four tests covering: (1) happy-path mints in the pipeline KV and
dispatches with the real secret name, (2)+(3) raises
DispatchUnavailableError when gh_pat or pipeline_kv_uri is missing
without side-effects, (4) emits 'pipeline_dispatched' log tagged by
triggered_by ('submission_created' | 'admin_retry').

Tests fail today because dispatch_helper does not exist; Task 4
implements it to make these green."
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Implement `dispatch_for_submission` in `services/pipeline_dispatch/dispatch_helper.py`

**Verifies:** pipeline-trust.AC5.1, pipeline-trust.AC5.4, pipeline-trust.AC6.1 (helper-level), pipeline-trust.AC6.2 (no DB write because helper never touches DB), pipeline-trust.AC6.3 (no orphan secret because helper bails before mint).

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/dispatch_helper.py`

**Implementation:**

Create a new module marked `# pattern: Imperative Shell` exposing one async function `dispatch_for_submission`. Lift the dispatch sequence out of `retry.py` (lines 36-92 — full body of `retry_dispatch`) into this helper, parameterized by `triggered_by: Literal['submission_created', 'admin_retry']`. The helper:

1. Asserts `settings.gh_pat` is set; raises `DispatchUnavailableError("Pipeline dispatch is not configured: RAC_GH_PAT is unset.")` otherwise.
2. Asserts `settings.pipeline_kv_uri` is non-empty; raises `DispatchUnavailableError("Pipeline dispatch is not configured: RAC_PIPELINE_KV_URI is unset.")` otherwise. **Both checks happen BEFORE any I/O**, ensuring AC6.3 (no orphan secret) is structural.
3. Calls `mint_callback_secret(submission.id, kv_uri=settings.pipeline_kv_uri, expiry_minutes=settings.pipeline_timeout_minutes * 2)` — note the change from `settings.kv_uri` to `settings.pipeline_kv_uri`.
4. Builds the payload via `build_dispatch_payload(submission, callback_base_url=settings.callback_base_url, callback_secret_name=secret_name)`.
5. Emits one structured log line: `logger.info("pipeline_dispatched", submission_id=str(submission.id), triggered_by=triggered_by)`.
6. Calls `gh_dispatch.dispatch(settings.gh_pipeline_owner, settings.gh_pipeline_repo, payload, auth_token=...)`.
7. Returns the dict `{submission_id, callback_url, dispatched_at}` (ISO 8601 UTC timestamp).

The new home for `DispatchUnavailableError` is `dispatch_helper.py` — move the class definition (currently `retry.py:29-33`) here, since both create flow and retry flow now import it from the helper. Keep a backwards-compatible re-export in `retry.py` for any callers that imported it from there: `from rac_control_plane.services.pipeline_dispatch.dispatch_helper import DispatchUnavailableError as DispatchUnavailableError`.

Module skeleton:

```python
# pattern: Imperative Shell
"""Shared dispatch helper for the build-and-scan pipeline.

Single entry point for both the original-create flow (services/submissions/
create.py) and the admin-retry flow (services/pipeline_dispatch/retry.py).
Mints the per-submission HMAC callback secret in the dedicated pipeline KV,
builds the dispatch payload, and POSTs repository_dispatch.

Design constraints:
  - DispatchUnavailableError is raised BEFORE any I/O when required config is
    missing; this guarantees no orphan KV secret and no DB row left behind.
  - Callback secrets ALWAYS land in settings.pipeline_kv_uri, never in
    settings.kv_uri (the platform KV); the strict separation is part of the
    pipeline identity blast-radius isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import structlog

from rac_control_plane.data.models import Submission
from rac_control_plane.services.pipeline_dispatch import github as gh_dispatch
from rac_control_plane.services.pipeline_dispatch.payload import build_dispatch_payload
from rac_control_plane.services.pipeline_dispatch.secret_mint import mint_callback_secret

logger = structlog.get_logger(__name__)


class DispatchUnavailableError(Exception):
    """Pipeline dispatch can't run because a required input is missing.

    The route layer maps this to HTTP 503. Carries a public-safe message.
    """


async def dispatch_for_submission(
    submission: Submission,
    *,
    settings: Any,
    triggered_by: Literal["submission_created", "admin_retry"],
) -> dict[str, str]:
    """Mint callback secret, build payload, POST repository_dispatch.

    Args:
        submission: ORM object; caller has already verified state == awaiting_scan.
        settings: Application Settings — needs gh_pat, gh_pipeline_owner,
            gh_pipeline_repo, pipeline_kv_uri, callback_base_url,
            pipeline_timeout_minutes.
        triggered_by: 'submission_created' on first dispatch, 'admin_retry'
            when the admin retry route invokes this. Tagged on the log line.

    Returns:
        {submission_id, callback_url, dispatched_at} (dispatched_at is ISO 8601 UTC).

    Raises:
        DispatchUnavailableError: gh_pat or pipeline_kv_uri unset.
    """
    pat = settings.gh_pat
    if not pat:
        raise DispatchUnavailableError(
            "Pipeline dispatch is not configured: RAC_GH_PAT is unset."
        )
    if not settings.pipeline_kv_uri:
        raise DispatchUnavailableError(
            "Pipeline dispatch is not configured: RAC_PIPELINE_KV_URI is unset."
        )

    auth_token = pat.get_secret_value()

    secret_name, _secret_value = await mint_callback_secret(
        submission.id,
        kv_uri=settings.pipeline_kv_uri,
        expiry_minutes=settings.pipeline_timeout_minutes * 2,
    )

    payload = build_dispatch_payload(
        submission,
        callback_base_url=settings.callback_base_url,
        callback_secret_name=secret_name,
    )

    logger.info(
        "pipeline_dispatched",
        submission_id=str(submission.id),
        triggered_by=triggered_by,
    )

    await gh_dispatch.dispatch(
        settings.gh_pipeline_owner,
        settings.gh_pipeline_repo,
        payload,
        auth_token=auth_token,
    )

    return {
        "submission_id": str(submission.id),
        "callback_url": payload["callback_url"],
        "dispatched_at": datetime.now(tz=UTC).isoformat(),
    }
```

**Verification:**

```bash
cd /home/sysop/rac/apps/control-plane/backend
uv run pytest tests/test_dispatch_helper.py -v
# Expected: all four tests from Task 3 now PASS (green phase).

uv run mypy src/rac_control_plane/services/pipeline_dispatch/dispatch_helper.py
# Expected: zero type errors.
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/dispatch_helper.py
git commit -m "control-plane(dispatch): add dispatch_for_submission helper

Single entry point for both the original-create flow and the admin-retry
flow. Mints callback secret in the dedicated pipeline KV (NEVER the
platform KV), builds payload, POSTs repository_dispatch. Raises
DispatchUnavailableError BEFORE any I/O when gh_pat or pipeline_kv_uri
is missing — guarantees no orphan KV secret on misconfig (AC6.3 by
construction).

DispatchUnavailableError is now defined here; retry.py re-exports for
backwards compat (Task 5)."
```
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_TASK_5 -->
### Task 5: Refactor `retry.py` to delegate to `dispatch_for_submission`

**Verifies:** pipeline-trust.AC5.3 (both create.py and retry.py route through helper).

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py`

**Implementation:**

Reduce `retry.py` to a thin wrapper around `dispatch_for_submission`. Preserve:
1. The function name `retry_dispatch` (existing callers in `api/routes/provisioning.py:304` continue to import it by this name).
2. The `admin_oid` audit-log line (emit `logger.info("pipeline_dispatch_retry_admin", submission_id=..., admin_oid=...)` BEFORE calling the helper, so the audit line lands even if dispatch then raises).
3. Re-export `DispatchUnavailableError` so any imports `from rac_control_plane.services.pipeline_dispatch.retry import DispatchUnavailableError` (e.g., `provisioning.py:24` if applicable — verify this import path on your branch) continue to work.

New file body:

```python
# pattern: Imperative Shell
"""Re-dispatch the build-and-scan pipeline for a stuck submission.

Used by the admin retry endpoint to recover submissions stuck in
awaiting_scan. Delegates to dispatch_for_submission for the dispatch
sequence; adds the admin_oid audit log line on top.
"""

from __future__ import annotations

from typing import Any

import structlog

from rac_control_plane.data.models import Submission
from rac_control_plane.services.pipeline_dispatch.dispatch_helper import (
    DispatchUnavailableError as DispatchUnavailableError,
    dispatch_for_submission,
)

logger = structlog.get_logger(__name__)


async def retry_dispatch(
    submission: Submission,
    *,
    settings: Any,
    admin_oid: str,
) -> dict[str, str]:
    """Re-dispatch the build-and-scan pipeline for one submission (admin op).

    Args:
        submission: ORM object; caller has already verified state == awaiting_scan.
        settings: Application Settings (see dispatch_for_submission).
        admin_oid: OID of the admin invoking the retry, for the audit log.

    Returns:
        See dispatch_for_submission.

    Raises:
        DispatchUnavailableError: re-raised from the helper.
    """
    logger.info(
        "pipeline_dispatch_retry_admin",
        submission_id=str(submission.id),
        admin_oid=admin_oid,
    )
    return await dispatch_for_submission(
        submission,
        settings=settings,
        triggered_by="admin_retry",
    )
```

The `as DispatchUnavailableError` re-export form silences `unused import` linters and is the standard idiom for re-exporting through a module.

**Verification:**

```bash
cd /home/sysop/rac/apps/control-plane/backend
uv run pytest tests/test_dispatch_retry_api.py -v
# Expected: all 155-line existing retry tests still pass (no regression).

uv run pytest tests/test_pipeline_dispatch.py -v
# Expected: still passes; the helper-level tests don't move here.

uv run mypy src/rac_control_plane/services/pipeline_dispatch/
# Expected: zero type errors.
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py
git commit -m "control-plane(dispatch): retry.py delegates to dispatch_for_submission

retry_dispatch is now a thin wrapper that emits the admin_oid audit
log line, then calls the shared helper with triggered_by='admin_retry'.
DispatchUnavailableError is re-exported for backwards compat with
existing imports."
```
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Refactor `create.py` — remove placeholder, drop `dispatch_fn`, thread `settings`

**Verifies:** pipeline-trust.AC5.1 (helper called with real secret name), pipeline-trust.AC5.2 (no PLACEHOLDER strings remain), pipeline-trust.AC5.3 (both code paths use helper).

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py` (function signature + lines 185-228)
- Modify: `apps/control-plane/backend/tests/test_create_submission.py` (every test that injects `dispatch_fn=...` switches to monkeypatch on `dispatch_for_submission`)

**Implementation:**

Two-part refactor: (a) `create_submission` drops `dispatch_fn` from its signature and accepts `settings: Settings` instead; the dispatch call is unconditional. (b) Existing tests in `test_create_submission.py` migrate from "inject a mock `dispatch_fn`" to "monkeypatch `rac_control_plane.services.submissions.create.dispatch_for_submission`".

#### 6a. Update `create_submission` signature and body

Open `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py`:

1. Remove `dispatch_fn` from the function signature.
2. Add `settings: Settings` to the signature (typed import from `rac_control_plane.settings`).
3. Replace the entire `if dispatch_fn is not None and submission.status == SubmissionStatus.awaiting_scan` block at lines 185-228 with the unconditional call below.

Replacement code at create.py:185-228 — note that **`DispatchUnavailableError` MUST be imported at module top-level** (NOT inside the function body) so the explicit `except DispatchUnavailableError:` clause resolves the class at parse time. Without that explicit clause, the broader `except Exception:` swallows `DispatchUnavailableError` (class hierarchy is what Python uses, not code comments) and AC6.1 + AC6.2 would silently break.

Top-level imports to add at the top of `create.py`:

```python
from rac_control_plane.services.pipeline_dispatch.dispatch_helper import (
    DispatchUnavailableError,
    dispatch_for_submission,
)
```

Replacement function-body code:

```python
    # Step 7: Trigger pipeline dispatch — single path through dispatch_for_submission.
    # The helper mints the per-submission HMAC callback secret in the dedicated
    # pipeline KV, builds the payload, and POSTs repository_dispatch. It raises
    # DispatchUnavailableError if RAC_GH_PAT or RAC_PIPELINE_KV_URI is unset.
    if submission.status == SubmissionStatus.awaiting_scan:
        try:
            await dispatch_for_submission(
                submission,
                settings=settings,
                triggered_by="submission_created",
            )
        except ValidationApiError:
            # Payload too large — mark submission failed, commit so the
            # pipeline_error state survives the re-raise, and propagate so the
            # route layer returns 422.
            submission.status = SubmissionStatus.pipeline_error
            await session.commit()
            logger.error(
                "pipeline_dispatch_payload_too_large",
                submission_id=str(submission.id),
            )
            raise
        except DispatchUnavailableError:
            # Loud-fail: propagate so the route layer maps to 503.
            # Re-raise BEFORE session.commit() — the submission row is
            # rolled back (AC6.2: no orphan row). The helper raised BEFORE
            # mint_callback_secret was called, so no orphan KV secret either
            # (AC6.3). MUST come before the broad `except Exception` clause
            # below — DispatchUnavailableError(Exception) inherits from
            # Exception, so without this explicit clause the broad-catch
            # would swallow it.
            raise
        except Exception as exc:
            # 5xx / network error — log, leave submission as awaiting_scan.
            # Operator retries via the admin endpoint.
            logger.error(
                "pipeline_dispatch_failed",
                submission_id=str(submission.id),
                error=str(exc),
            )
            # Do NOT re-raise; still return 201 to the user.
```

#### 6b. Update the route to pass `settings`

`api/routes/submissions.py` already calls `settings = get_settings()` at the top of the route (line 142). Pass it into `create_submission`:

```python
    submission = await create_submission(
        session,
        principal,
        request,
        existing_slugs,
        settings=settings,
        emit_submission_metric=lambda status: submission_counter.add(1, {"status": status}),
        detection_fn=detection_fn,
        validate_pi_fn=_validate_pi,
    )
```

(This is the call site already noted in Task 7 — Tasks 6 and 7 both touch the route. Make the signature change in Task 6, and the try/except wrap in Task 7.)

#### 6c. Migrate existing tests in `test_create_submission.py`

Every test that currently calls `create_submission(..., dispatch_fn=mock)` migrates to:

```python
def test_xxx(monkeypatch, ...):
    mock_dispatch = AsyncMock()
    monkeypatch.setattr(
        "rac_control_plane.services.submissions.create.dispatch_for_submission",
        mock_dispatch,
    )
    # ... test body, now calls create_submission WITHOUT dispatch_fn
    submission = await create_submission(
        session, principal, request, existing_slugs,
        settings=settings,  # required now
        detection_fn=detection_fn,
        validate_pi_fn=_validate_pi,
    )
    # Assert mock_dispatch was called with the right args.
    mock_dispatch.assert_awaited_once_with(submission, settings=settings, triggered_by="submission_created")
```

The exact migration depends on each test's existing structure. The pattern is mechanical: every `dispatch_fn=` keyword argument is replaced by a `monkeypatch.setattr` on the import site, and assertions on the mock change shape from "called with payload dict" to "called with submission + settings + triggered_by".

Estimate: ~15 tests in `test_create_submission.py` use `dispatch_fn`; each is a 3-line edit. About 30 minutes of mechanical work.

**Verification:**

```bash
cd /home/sysop/rac

# AC5.2: grep for PLACEHOLDER returns no results in dispatch code paths
grep -rn "PLACEHOLDER" apps/control-plane/backend/src/rac_control_plane/services/
# Expected: no output.

# AC5.3: both code paths route through dispatch_for_submission
grep -l "dispatch_for_submission" \
  apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py \
  apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py
# Expected: both files listed.

# Production code does NOT call get_settings() inside create_submission
grep -n "get_settings" apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py
# Expected: zero matches in the function body (settings flows in via DI).

# create_submission no longer accepts dispatch_fn
grep -n "dispatch_fn" apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py
# Expected: zero matches.

cd apps/control-plane/backend
uv run pytest tests/test_create_submission.py -v
# Expected: all tests still green after the migration.

uv run mypy src/rac_control_plane/services/submissions/create.py
# Expected: zero type errors.
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py \
        apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py \
        apps/control-plane/backend/tests/test_create_submission.py
git commit -m "control-plane(submissions/create): route dispatch through helper, drop dispatch_fn

Eliminates the placeholder-callback-secret bug. Production code path
unconditionally calls dispatch_for_submission, which mints a real
HMAC secret in the pipeline KV before building the payload.

create_submission no longer accepts dispatch_fn (it was a test-only
parameter that leaked into production). Settings is now threaded as
a typed parameter (DI), not pulled from get_settings() mid-function.
Existing tests migrated to monkeypatch dispatch_for_submission at
the import site — same blast radius, cleaner production code.

AC5.1, AC5.2, AC5.3 satisfied."
```
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Remove silent-no-op from `_build_dispatch_fn`; map `DispatchUnavailableError` → 503 in create-submission route

**Verifies:** pipeline-trust.AC6.1 (503 on missing PAT), pipeline-trust.AC6.5 (silent-no-op log line gone).

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py:79-104` (delete `_build_dispatch_fn` entirely) AND `submissions.py:107-204` (the `post_submission` route).

**Implementation:**

1. Delete the `_build_dispatch_fn` function (lines 79-104). It's the only caller of the silent-no-op log line; removing it deletes that line by construction (AC6.5).

2. Delete the call site at `submissions.py:145` (`dispatch_fn = _build_dispatch_fn(settings)`) and the `dispatch_fn=dispatch_fn` argument at line 179.

3. Update the `create_submission(...)` call to NOT pass `dispatch_fn` (Task 6 already removed it from the signature). Pass `settings=settings` instead — Task 6 added `settings` as a required parameter.

4. Wrap the `create_submission(...)` call in a try/except chain that catches `DispatchUnavailableError` and maps it to `ServiceUnavailableError`. Mirror `provisioning.py:303-310`. Place the import at the top of the file.

Updated `submissions.py` excerpt (replaces lines ~140-185):

```python
    settings = get_settings()

    # Build detection function — source rules from app.state.rules if populated,
    # else let _make_detection_fn call load_rules() lazily inside the closure.
    import rac_control_plane.main as _main_mod  # noqa: PLC0415
    _app_state = getattr(getattr(_main_mod, "app", None), "state", None)
    _cached_rules: dict[str, Any] | None = (
        getattr(_app_state, "rules", None) if _app_state else None
    )

    detection_fn = _make_detection_fn(
        principal_kind=principal.kind,
        rules=_cached_rules,
    )

    # Get existing slugs to avoid collisions
    existing_slugs = await get_existing_slugs(session)

    # Build PI validation function closure
    from rac_control_plane.services.ownership import graph_gateway, pi_validation

    async def _validate_pi(oid: Any) -> Any:
        user = await graph_gateway.get_user(oid)
        return pi_validation.is_valid_pi(user)

    # Create submission. Production path internally calls
    # dispatch_for_submission; missing RAC_GH_PAT / RAC_PIPELINE_KV_URI
    # surfaces as DispatchUnavailableError → 503 here, BEFORE
    # session.commit, so no DB row is left behind.
    try:
        submission = await create_submission(
            session,
            principal,
            request,
            existing_slugs,
            settings=settings,
            emit_submission_metric=lambda status: submission_counter.add(1, {"status": status}),
            detection_fn=detection_fn,
            validate_pi_fn=_validate_pi,
        )
    except DispatchUnavailableError as exc:
        raise ServiceUnavailableError(public_message=str(exc)) from exc

    # Commit the transaction
    await session.commit()
    # ... rest unchanged
```

5. New imports at the top of `submissions.py`:

```python
from rac_control_plane.api.errors import ServiceUnavailableError
from rac_control_plane.services.pipeline_dispatch.dispatch_helper import (
    DispatchUnavailableError,
)
```

(Verify the existing `ServiceUnavailableError` import path against what `provisioning.py` uses — adjust if the repo uses a different module path.)

**Testing (TDD — write failing tests first):**

Add three new tests to `apps/control-plane/backend/tests/test_submissions_api.py`. Each test exercises real Postgres + a mocked KV + mocked GH dispatch (use the `respx` or `httpx_mock` fixture if used by existing tests; otherwise `monkeypatch` on `gh_dispatch.dispatch`).

1. `test_create_submission_happy_path_dispatches_with_real_secret_name`
   - **Verifies:** AC5.1.
   - Setup: full happy-path env (gh_pat set, pipeline_kv_uri set, mocked KV `set_secret`, captured GH dispatch).
   - Assert: response 201; mock GH dispatch was called with `client_payload['callback_secret_name'] == f'rac-pipeline-cb-{response.json()["id"]}'`.

2. `test_create_submission_no_orphan_row_on_dispatch_503`
   - **Verifies:** AC6.1, AC6.2, AC6.3.
   - Setup: `gh_pat` is None on Settings (`monkeypatch.delenv("RAC_GH_PAT")` + `get_settings.cache_clear()`).
   - Assert: response 503 with body shape `{"code": "service_unavailable", "message": "...RAC_GH_PAT...", "correlation_id": "..."}`. Then query the DB for the `submission` table — `count == 0` (AC6.2). The mocked KV `set_secret` was never called (AC6.3).

3. `test_create_submission_idempotency_replays_503_on_retry`
   - **Verifies:** AC6.4.
   - Setup: same as #2 + use `Idempotency-Key: <uuid>` header on the request.
   - Action: POST once → expect 503. POST again with same key/body → expect 503 again.
   - Assert: both responses are 503 with the same correlation_id (idempotency replay returns the stored response). The second request did NOT hit `create_submission` (verify via spy on the service function).

Also add one assertion test:

4. `test_no_silent_no_op_log_line_on_dispatch_skip` (or just a grep-based test)
   - **Verifies:** AC6.5.
   - Inspect `apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py` source via `pathlib`; assert the literal string `'pipeline_dispatch_skipped_no_auth_token'` is NOT present. (This is a structural test; runs in <1ms.)

**Verification:**

```bash
cd /home/sysop/rac/apps/control-plane/backend

# Failing-first phase (run BEFORE Task 7 implementation):
uv run pytest tests/test_submissions_api.py::test_create_submission_no_orphan_row_on_dispatch_503 -v
# Expected: FAILS (route still has silent-no-op).

# After implementation:
uv run pytest tests/test_submissions_api.py -v
# Expected: ALL tests pass, including the four new ones.

# AC6.5 grep — log line literal is gone
grep -rn "pipeline_dispatch_skipped_no_auth_token" apps/control-plane/backend/src/
# Expected: zero matches.

# AC6.5 grep — _build_dispatch_fn function itself is gone (the silent-no-op factory)
grep -rn "_build_dispatch_fn" apps/control-plane/backend/src/
# Expected: zero matches.

# Full backend suite
uv run pytest -x
# Expected: 656+ tests pass (existing 652 + Task 1's 2 + Task 3's 4 + Task 7's 4 + minor adjustments to test_create_submission.py if needed for the test-path semantics).

# mypy clean
uv run mypy src/
# Expected: zero errors.
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py apps/control-plane/backend/tests/test_submissions_api.py
git commit -m "control-plane(submissions): map DispatchUnavailableError -> 503

Removes _build_dispatch_fn (the silent-no-op factory) entirely; the
production dispatch path is now inside create_submission via
dispatch_for_submission. The create-submission route catches
DispatchUnavailableError BEFORE session.commit, so a 503 response
guarantees no orphan submission row (AC6.2) and no orphan KV secret
(AC6.3 — helper bails before mint).

Idempotency middleware records the 503 like any other status, so a
replay with the same Idempotency-Key returns the cached 503 (AC6.4).

The 'pipeline_dispatch_skipped_no_auth_token' log line is gone
(AC6.5).

Four new tests in test_submissions_api.py lock in AC5.1, AC6.1,
AC6.2, AC6.3, AC6.4, AC6.5."
```
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Final verification — full test suite, structural grep checks

**Verifies:** all of pipeline-trust.AC5.* and pipeline-trust.AC6.*.

**Files:** none (verification only).

**Implementation:**

Run a complete check sweep at Phase 3 exit.

**Verification:**

```bash
cd /home/sysop/rac/apps/control-plane/backend

# Full test suite green
uv run pytest -x --tb=short
# Expected: 656+ pass, 0 fail.

# mypy clean
uv run mypy src/
# Expected: zero errors.

# AC5.2 — no PLACEHOLDER strings in dispatch code
grep -rn "PLACEHOLDER\|placeholder" apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/ apps/control-plane/backend/src/rac_control_plane/services/submissions/
# Expected: zero matches.

# AC5.3 — both create.py and retry.py route through dispatch_for_submission
grep -l "dispatch_for_submission\|retry_dispatch" \
  apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py \
  apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py
# Expected: both files listed.

# AC6.5 — silent-no-op log line gone
grep -rn "pipeline_dispatch_skipped_no_auth_token" apps/control-plane/backend/src/
# Expected: zero matches.

# AC6.5 — _build_dispatch_fn function structurally removed
grep -rn "_build_dispatch_fn" apps/control-plane/backend/src/
# Expected: zero matches.

# Working tree clean
git status --short
# Expected: empty (everything from Tasks 1-7 committed).
```

If any check fails, fix before declaring Phase 3 done.

**Commit:** none (verification step).
<!-- END_TASK_8 -->

---

## Phase 3 Done When

- All eight task verifications pass.
- Full backend test suite (656+ tests including newly added) is green.
- `mypy src/` is clean.
- `_build_dispatch_fn` and `pipeline_dispatch_skipped_no_auth_token` are completely removed from the codebase.
- `PLACEHOLDER` strings are absent from dispatch-related modules.
- Both `create.py` and `retry.py` import and call `dispatch_for_submission`.
- `RAC_PIPELINE_KV_URI` env var is wired through `Settings` and into the control-plane container app's bicep env block.
- Working tree is clean.
- No live deploy yet (Phase 5).
