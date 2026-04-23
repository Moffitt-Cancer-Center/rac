# Phase 5: Approval workflow and Tier 3 provisioning

**Goal:** Two-stage approval (Research → IT) works end-to-end. On final IT approval, the Control Plane provisions Tier 3 dynamic resources via Azure SDK: a new ACA app with `min-replicas=0` (HTTP scaler), DNS A-record under `${PARENT_DOMAIN}`, per-app JWT signing key in Key Vault, asset mounts (Azure Files — note Blob is not directly mountable; Phase 8 covers Blob-backed assets via a companion copy step). Ownership model is populated: every submission validates `pi_principal_id` against Microsoft Graph. A nightly job sweeps Graph for deactivated PIs and flags affected apps.

**Architecture:** FCIS. Approval transitions are pure logic over the FSM defined in Phase 2. Entra group-membership checks (is this principal in the research or IT approver group?) are Shell but isolated behind a thin `IdentityGateway`. Azure SDK wrappers are Shell: `provisioning/aca.py`, `provisioning/dns.py`, `provisioning/keys.py`. The provisioning orchestrator (Shell) calls the wrappers in sequence, writes `app` row with `current_submission_id`, transitions submission to `deployed` on success, and records all steps in `approval_event`. The Graph sweep job runs via a scheduled ACA job (`Microsoft.App/jobs` with `triggerType: Schedule`) — separate from the Control Plane's web service.

**Tech Stack:** `azure-mgmt-appcontainers` 3.2.0, `azure-mgmt-dns` 9.0.0, `azure-keyvault-keys` 4.8.0, `msgraph-sdk-python` 1.0+, `azure-identity` (DefaultAzureCredential via user-assigned managed identity attached to Control Plane's ACA app). Storage for assets → Azure Files (ACA does not support direct Blob mounts as of 2026); Blob-backed external URLs are copied to the Azure Files share by a small helper (this phase stubs the mount wiring; Phase 8 owns asset semantics).

**Scope:** Phase 5 of 8.

**Codebase verified:** 2026-04-23 — Phase 2 delivers Control Plane + FSM; Phase 3 delivers pipeline + scan results (submissions can now reach `awaiting_research_review`). No `provisioning/` or `ownership/` subpackages exist yet. Azure SDK packages are not yet in `pyproject.toml` — Task 1 adds them.

**Design gap surfaced by investigation:**
- ACA scale-to-zero (`min-replicas=0`) only works with event-based scalers (HTTP, TCP, custom KEDA). CPU/Memory scalers do NOT support zero. Implementation pins an HTTP scaler with concurrency threshold 100 so all researcher apps scale to zero. No design change needed; documented here.
- ACA storage mounts accept Azure Files but NOT Azure Blob directly. Design language ("Azure Files CSI or Blob mounts") reflects this uncertainty. Decision: **use Azure Files throughout**; any `upload`/`external_url` asset is placed into a per-app Azure Files share by a helper in Phase 8. This Phase 5 task scaffolds the empty Files share per app; Phase 8 populates it.
- Azure Files mounts in ACA require **storage account key** auth (not managed identity). Decision: store the per-env storage account key as a secret in Key Vault; the Control Plane fetches it at provision time and passes it to ACA. Rotate yearly; this is an operational note for the bootstrap runbook (update in Task 1).

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC2: Researchers can submit applications end-to-end (FSM completion)
- **rac-v1.AC2.2 Success:** A submission progresses through `awaiting_scan → awaiting_research_review → awaiting_it_review → approved → deployed` when the scan passes and both approvers approve, with correct state transitions and timestamps recorded.

### rac-v1.AC6: Approved apps deploy automatically
- **rac-v1.AC6.1 Success:** Final IT approval triggers Tier 3 provisioning: an ACA app is created with the correct image digest, env vars, scaling rules (`min-replicas=0`), and tags; a DNS A-record is inserted; per-app signing key is created in Key Vault; submission transitions to `deployed`.
- **rac-v1.AC6.2 Success:** The app resolves at `https://<slug>.${PARENT_DOMAIN}`; when no replicas are warm, the shim serves the "waking up" interstitial, wakes the app, and redirects to the original URL within the configured wake budget. **(Note: cold-start interstitial is Phase 6's shim; this phase verifies DNS resolves and the ACA app responds when a request arrives. Interstitial rendering is Phase 6.)**
- **rac-v1.AC6.3 Failure:** A provisioning error (e.g., DNS conflict, quota exhaustion) surfaces in the admin UI with a retry control; the submission remains `approved` but not `deployed` until resolution.
- **rac-v1.AC6.4 Edge:** A re-submitted app (new approved submission for an existing slug) updates `app.current_submission_id` atomically; the previous image is retained in ACR with the old `submission_id` tag so history is recoverable.

### rac-v1.AC9: Ownership model is wired
- **rac-v1.AC9.1 Success:** A submission captures `pi_principal_id` (validated as a real Entra principal via Graph) and `dept_fallback` as required fields.
- **rac-v1.AC9.2 Success:** The nightly Graph sweep detects a PI whose Entra account is deactivated and flags every app owned by that PI with a "Owner deactivated — dept review pending" status visible to admins.
- **rac-v1.AC9.3 Edge:** An app's `pi_principal_id` can be transferred without losing audit history; historical `approval_event` rows retain their original `actor_principal_id` unchanged.

### rac-v1.AC10: Observability — approval duration metric (approval portion)
- **rac-v1.AC10.2 Success (approvals portion):** `rac.approvals.time_to_decision_seconds` histogram is recorded at each IT or research approval decision, with wall-clock time measured from `submission.created_at` to the approval moment.

### rac-v1.AC11: Cost attribution works
- **rac-v1.AC11.1 Success (Tier 3 portion):** Every Tier 3 Azure resource (ACA app, DNS record set, Key Vault key, Azure Files share) carries `rac_app_slug`, `rac_pi_principal_id`, `rac_submission_id`, `rac_env` tags at creation.
- **rac-v1.AC11.2 Success:** The nightly Azure Cost Management export job ingests daily cost data into the `cost_snapshot_monthly` table, tagged by `rac_app_slug`; the admin dashboard surfaces per-app month-to-date spend.
- **rac-v1.AC11.3 Edge:** Apps with `min-replicas=0` that are idle for 30+ days appear in a "scale-to-zero savings" row in the admin cost dashboard, showing estimated savings from zero-replica idling.

**Verifies:** Functionality phase. Each task names which AC cases it tests.

---

## File Classification Policy

- `provisioning/aca.py`, `dns.py`, `keys.py`, `files.py`: Imperative Shell (Azure SDK calls).
- `services/approvals/fsm_extensions.py`: Functional Core (pure transitions).
- `services/approvals/events.py`: Functional Core (event dataclasses).
- `services/ownership/graph_sweep.py`: Imperative Shell (Graph calls).
- `services/ownership/deactivation_logic.py`: Functional Core (pure decision "is this PI deactivated given Graph response").
- `api/routes/approvals.py`: Shell.
- React components: per Phase 2 conventions.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Azure SDK dependencies + managed identity wiring

**Verifies:** Foundation for AC6, AC9, AC11.1 (Tier 3)

**Files:**
- Modify: `apps/control-plane/backend/pyproject.toml` (add deps)
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/credentials.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/settings.py` additions (add Azure resource scoping fields: subscription_id, resource_group, aca_env_resource_id, dns_zone_name, files_storage_account_name, files_storage_account_key_kv_secret_name, managed_identity_resource_id, controlplane_managed_identity_client_id, graph_app_only_client_id — most already present; add those missing)
- Modify: `docs/runbooks/bootstrap.md` (document the managed-identity role assignments listed below)
- Create: `apps/control-plane/backend/tests/test_provisioning_credentials.py`

**Implementation:**

Add deps: `azure-mgmt-appcontainers>=3.2`, `azure-mgmt-dns>=9.0`, `azure-keyvault-keys>=4.8`, `azure-mgmt-storage>=21.1`, `msgraph-sdk>=1.0`, `azure-identity>=1.19`. `uv lock` to regenerate.

`provisioning/credentials.py`:
- `get_azure_credential() -> DefaultAzureCredential`: singleton, configured with `managed_identity_client_id=settings.controlplane_managed_identity_client_id` so ACA's user-assigned MI is preferred over system-assigned.
- `get_graph_client() -> GraphServiceClient`: uses the same credential; scopes `https://graph.microsoft.com/.default`.
- All SDK client builders (`aca_client`, `dns_client`, `key_client`, `storage_client`) go through this module for consistency.

Bootstrap runbook additions:
- Phase 1 Task 12B creates the managed identities `id-rac-controlplane-<env>` and `id-rac-shim-<env>` via `infra/modules/managed-identity.bicep`. **This task (Phase 5 Task 1) does NOT create or modify that Bicep module.** However, it triggers the **Phase 1 re-deploy loop**: after Phase 1 runs for the first time and outputs `controlPlaneMiPrincipalId`, the operator must re-run `infra-deploy` with `controlPlaneIdentityPrincipalId` set to that value so the DNS Zone Contributor role assignment is created. See `docs/runbooks/bootstrap.md` step 8 (updated by Phase 1 Task 12B) for the exact sequence.

- Role assignments for the Control Plane's MI (scoped as tightly as possible — all assigned by Phase 1 Task 12B's `managed-identity.bicep` module):
  - `Contributor` on the Tier 3 resource group `rg-rac-tier3-<env>` (ACA app create/update) — consider splitting to a custom role covering only `Microsoft.App/*` on containerApps + `Microsoft.ContainerRegistry/registries/pull` on ACR.
  - `DNS Zone Contributor` on the DNS zone (conditional on `controlPlaneIdentityPrincipalId` being set — requires Phase 1 second-pass re-deploy).
  - `Key Vault Crypto Officer` on the platform Key Vault.
  - `Storage Account Key Operator Service Role` + `Contributor` on the Tier-3 Azure Files storage account (for per-app share creation + key fetch).
  - Microsoft Graph app permission `User.Read.All` with admin consent (Tier 1 step — already in bootstrap).

Tier 3 resource group `rg-rac-tier3-<env>` is separate from Tier 2's `rg-rac-<env>` to isolate dynamic resources. Phase 1 Task 13 pre-creates this resource group empty; it is populated with researcher ACA apps by the provisioning orchestrator in Phase 5 Task 6.

Tests:
- `test_provisioning_credentials.py`: verify `get_azure_credential()` returns a `DefaultAzureCredential` with the expected `managed_identity_client_id`. Use `pytest-mock` to patch environment; no real Azure calls.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_provisioning_credentials.py -v
# Confirm Phase 1 managed-identity.bicep compiled successfully (should already be done)
az bicep build --file /home/sysop/rac/infra/modules/managed-identity.bicep
```

**Commit:** `feat(control-plane): Azure SDK deps + managed identity credentials`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: PI validation via Microsoft Graph (on submission create)

**Verifies:** `rac-v1.AC9.1`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/ownership/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/ownership/graph_gateway.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/ownership/pi_validation.py` (pattern: Functional Core)
- Modify: `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py` (call validator before DB insert)
- Create: `apps/control-plane/backend/tests/test_pi_validation.py`

**Implementation:**

`graph_gateway.py`: thin Shell wrapper `async def get_user(oid: UUID) -> GraphUser | None`, `async def get_users_batch(oids: list[UUID]) -> dict[UUID, GraphUser | None]`. Uses `msgraph-sdk-python`. Retry on 429 with backoff. Cache positive results in-process for `settings.graph_user_cache_ttl_seconds` (default 300). Missing users return None (not exception). `GraphUser` is a frozen dataclass: `oid`, `account_enabled`, `display_name`, `user_principal_name`, `department`.

`pi_validation.py` (pure):
- `def is_valid_pi(user: GraphUser | None) -> ValidationResult`: returns `Ok` or `Invalid(reason)`. `Invalid` reasons: `"not_found"`, `"account_disabled"`. Property tests.

`create.py` updated: after parsing `pi_principal_id` from request, call `graph_gateway.get_user(req.pi_principal_id)`; pass to `pi_validation.is_valid_pi`. On invalid → `ValidationApiError("invalid_pi", "PI <oid> is not a current Entra principal: <reason>")` before DB write.

`tests/test_pi_validation.py`:
- Valid active user → `Ok`.
- Disabled user → `Invalid(account_disabled)`.
- Not found → `Invalid(not_found)`.
- Property test.
- Integration test in `test_submissions_api.py` (update Phase 2 test) asserting submissions with unknown PIs return 422 and no row created.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_pi_validation.py -v
```

**Commit:** `feat(control-plane): PI validation via Microsoft Graph`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-4) -->

<!-- START_TASK_3 -->
### Task 3: Approval FSM extensions + approver role checks

**Verifies:** `rac-v1.AC2.2` (approval transitions)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/approvals/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/approvals/fsm_extensions.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/approvals/role_check.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/approvals/identity_gateway.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_approvals_fsm.py`
- Create: `apps/control-plane/backend/tests/test_approvals_role_check.py`

**Implementation:**

Phase 2 defined the core FSM. Phase 5 extends with approval-specific events: `ResearchApproved`, `ResearchRejected`, `ITApproved`, `ITRejected`, `ProvisioningCompleted`, `ProvisioningFailed` (new — drops back to `approved` on retryable errors).

`fsm_extensions.py`: add new transitions to the registered table; assert the full state graph still matches the design diagram. Pure.

`role_check.py` (pure): `def principal_can_approve_stage(principal: Principal, stage: Literal['research','it']) -> bool`. Maps stage to the role name (`settings.approver_role_research` or `settings.approver_role_it`); returns `stage_role in principal.roles`. Property test: principal with neither role cannot approve either stage; principal with both can approve both.

`identity_gateway.py`: `async def get_principal_group_memberships(oid) -> frozenset[str]`. Uses Graph `getMemberGroups` or `memberOf`. Cached 5 min.

Integration: Phase 2's `current_principal` dependency fetches roles on each request; `role_check` is pure and composes cleanly.

Tests:
- FSM: every approval transition (positive and negative) is in the table.
- Role check: truth table across role combinations.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_approvals_fsm.py apps/control-plane/backend/tests/test_approvals_role_check.py -v
```

**Commit:** `feat(control-plane): approval FSM + role checks`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Approval API endpoints

**Verifies:** `rac-v1.AC2.2`, `rac-v1.AC10.2` (approvals histogram — wires the call site declared in Phase 2 Task 13B)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/approvals.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/schemas/approvals.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/approvals/record.py` (pattern: Imperative Shell — writes approval_event + transitions status + emits approval duration metric)
- Create: `apps/control-plane/backend/tests/test_approvals_api.py`

**Implementation:**

Endpoints (auth required, stage-scoped):
- `POST /submissions/{id}/approvals/research`: body `{decision: 'approve'|'reject'|'request_changes', notes?: str}`. Permission: `principal_can_approve_stage(principal, 'research')`. Writes `approval_event(kind='research_decision', actor_principal_id=principal.oid, decision, notes)` and transitions submission via `fsm.transition` (ResearchApproved / ResearchRejected / back-to-`needs_assistance` on `request_changes`). Returns 200 with updated submission.
- `POST /submissions/{id}/approvals/it`: same shape for IT stage. On `approve` → transitions to `approved` and enqueues provisioning (Task 6) via background task. On `reject` → `it_rejected`.

The submission FSM's `request_changes` event transitions back to `needs_assistance`; researchers resolve and the submission re-enters the approval chain when they act on findings/notes.

**Approval duration metric (AC10.2):** In `services/approvals/record.py`, after writing the `approval_event` row for `approve` or `reject` decisions, compute `elapsed_seconds = (datetime.now(timezone.utc) - submission.created_at).total_seconds()` and call `metrics.approval_duration_histogram.record(elapsed_seconds, {"decision": decision, "stage": stage})` where `stage` is `"research"` or `"it"`. The `approval_duration_histogram` is declared in `rac_control_plane/metrics.py` (Phase 2 Task 13B); import it there. This replaces the `TODO(phase5)` comment left in Phase 2 Task 13B.

Tests:
- Researcher in research group approves → status `awaiting_it_review`; `approval_event` row with correct actor.
- Researcher without role → 403.
- IT approves when submission not in `awaiting_it_review` → 409 (invalid transition).
- Rejection at either stage transitions to the correct terminal.
- `request_changes` transitions back to `needs_assistance`.
- End-to-end: scan passes → research approves → IT approves → submission reaches `approved` (Task 6 then transitions to `deployed`).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_approvals_api.py -v
```

**Commit:** `feat(control-plane): approval endpoints (research + IT)`
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 5-7) -->

<!-- START_TASK_5 -->
### Task 5: Azure SDK provisioning wrappers (ACA, DNS, Key Vault, Files)

**Verifies:** `rac-v1.AC6.1`, `rac-v1.AC11.1` (Tier 3)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/aca.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/dns.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/keys.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/files.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/provisioning/tag_builder.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_provisioning_*` (one test file per wrapper, mocks the SDK, verifies the constructed request)

**Implementation:**

`tag_builder.py` (pure): `def build_tier3_tags(app: App, submission: Submission, env: str) -> dict[str, str]`:
```python
return {
    "rac_env": env,
    "rac_app_slug": app.slug,
    "rac_pi_principal_id": str(submission.pi_principal_id),
    "rac_submission_id": str(submission.id),
    "rac_managed_by": "control-plane",
}
```
Property test: every result contains the four required tags from AC11.1 (Tier 3).

`aca.py`: `async def create_or_update_app(app, submission, image_ref, env_vars, azure_files_share_name, tags) -> ACAAppResult`. Builds `ContainerApp` model:
- `location = settings.azure_location`
- `environment_id = settings.aca_env_resource_id`
- `workload_profile_name = "Consumption"`
- `identity = ManagedServiceIdentity(type=UserAssigned, user_assigned_identities={settings.managed_identity_resource_id: {}})` — attaches the Control Plane MI (or a dedicated per-app MI in a future iteration)
- `tags = tags`
- `configuration = Configuration(ingress=Ingress(external=False, target_port=app.target_port, transport="http"), secrets=[...], registries=[RegistryCredentials(server=acr_login_server, identity=managed_identity_resource_id)])`
- `template = Template(containers=[Container(name=app.slug, image=image_ref, env=env_vars, volume_mounts=volume_mounts, resources=ContainerResources(cpu=app.cpu_cores, memory=app.memory_gb))], scale=Scale(min_replicas=0, max_replicas=10, rules=[ScaleRule(name='http', http=HttpScaleRule(metadata={"concurrentRequests":"100"}))]), volumes=[Volume(name="assets", storage_type="AzureFile", storage_name=azure_files_share_name)])`

**Ingress**: `external=False` — only the Shim (Phase 6) reaches these apps. Ingress is internal to the ACA environment.
**Scale rule**: HTTP-based with 100 concurrent requests — ensures `min_replicas=0` is honored (CPU/Memory scalers would block zero).
**Volume mount**: Always mount a per-app Azure Files share at `/mnt/assets` even if empty; Phase 8 populates it.

Handles operation as long-running poller; awaits `.result()`.

On transient errors (`HttpResponseError` with `retry_after` header): raise a typed `TransientProvisioningError`. On permanent errors (quota, DNS conflict): raise `ProvisioningError(code, detail)` — surfaced in UI (AC6.3).

`dns.py`: `async def upsert_a_record(zone_name, subdomain, ip_address, tags) -> str` (returns the record set resource ID). Uses `DnsManagementClient.record_sets.create_or_update(resource_type='A', ...)`. Tags applied. Idempotent.

`keys.py`: `async def create_signing_key(app_slug, tags) -> KeyIdentifier`. Uses `azure-keyvault-keys.KeyClient.create_ec_key(name=f"rac-app-{app_slug}-v1", curve=KeyCurveName.p_256, key_operations=["sign","verify"])`. Tags applied via `tags` keyword (Key Vault keys support tags). Stores `kid` in `signing_key_version` row (FK to `app`).

`files.py`: `async def ensure_app_share(storage_account_name, share_name, tags) -> str`. Uses `azure-mgmt-storage.FileSharesOperations.create` to create a share named after the app's slug. Idempotent. Tags applied.

Each wrapper has a test file mocking the SDK client; each test verifies:
- Correct model is passed to the SDK (`mock.assert_called_with(...)`).
- Tags from `tag_builder.build_tier3_tags` appear on every call.
- `min_replicas=0` present (AC6.1).
- HTTP scaler present.
- Transient errors converted; permanent errors surface.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_provisioning_*.py -v
```

**Commit:** `feat(control-plane): Azure SDK provisioning wrappers`
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Tier 3 provisioning orchestrator

**Verifies:** `rac-v1.AC6.1`, `rac-v1.AC6.3`, `rac-v1.AC6.4`, `rac-v1.AC11.1` (Tier 3)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/provisioning/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/provisioning/orchestrator.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/provisioning/retry_policy.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/data/app_repo.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_provisioning_orchestrator.py`

**Implementation:**

`retry_policy.py` (pure): decides whether a `ProvisioningError` is retryable, number of attempts, backoff.

`orchestrator.py`: `async def provision_submission(session, submission: Submission) -> ProvisioningOutcome`:
1. Look up or create `app` row keyed on `slug`. If exists (re-submission, AC6.4) → `app.current_submission_id` becomes the new submission ID in an `UPDATE` inside a single transaction (atomic per AC6.4).
2. Load App Gateway IP from `settings.app_gateway_public_ip`.
3. Build tags via `tag_builder.build_tier3_tags(app, submission, settings.env)`.
4. `files.ensure_app_share(...)` (creates the share if missing).
5. `keys.create_signing_key(app.slug, tags)` — only on first deploy; re-submissions reuse existing key (key rotation is a separate process per design).
6. `aca.create_or_update_app(...)` — uses the image tag `{acr}/{slug}:{submission_id}`, which the Phase 3 pipeline already pushed. The ACR pull credentials come via the managed identity. The old image for the old submission stays in ACR tagged `{slug}:{old_submission_id}` (AC6.4 history retention — ACR lifecycle policy will age it out, not this code).
7. `dns.upsert_a_record(...)` — points `<slug>.${PARENT_DOMAIN}` at the App Gateway IP.
8. Transition submission `approved → deployed` via FSM (`ProvisioningCompleted`).
9. Write `approval_event` entries for each step completed and for the overall outcome.

On `ProvisioningError`:
- Retryable: retry per policy (max 3 attempts).
- Permanent: set submission state to **stay at `approved`** (NOT `deployed`), write `approval_event(kind='provisioning_failed', detail=err.detail)`. Admin UI shows retry control (AC6.3).

Idempotency: every SDK call is idempotent; re-running `provision_submission` after a partial failure resumes at whichever step has not completed (verified by reading the DB / Azure state before each step).

`app_repo.py`: `get_by_slug`, `upsert_app_on_approved_submission` (atomic SET `current_submission_id` in a transaction).

Tests: extensive mock-SDK tests covering:
- Happy path: all SDK calls succeed → submission `deployed`, `app.current_submission_id` set, `approval_event` rows for each step.
- Re-submission (slug exists) → `current_submission_id` updated atomically; AC6.4 verified by asserting the previous `submission_id`-tagged image reference still exists in the `scan_result` row (no delete).
- DNS conflict → `ProvisioningError(code='dns_conflict')`, submission stays `approved`, `approval_event(kind='provisioning_failed')` row, retry button works on second call (AC6.3).
- ACA creation hits transient error once, succeeds on retry → submission deployed.
- Tag assertion: the tags dict passed to every SDK call contains all four AC11.1 tags.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_provisioning_orchestrator.py -v
```

**Commit:** `feat(control-plane): Tier 3 provisioning orchestrator`
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Admin retry UI for failed provisioning

**Verifies:** `rac-v1.AC6.3`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/provisioning.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/frontend/src/features/admin/provisioning/failed-provisions.tsx`
- Create: `apps/control-plane/backend/tests/test_provisioning_retry_api.py`

**Implementation:**

Backend: `POST /submissions/{id}/provisioning/retry` — admin-only; re-runs `orchestrator.provision_submission`; returns 200 with updated submission.

Frontend: admin page listing submissions in `approved`-but-not-`deployed` state with failure reason + retry button + disable-retry-when-spinning UX.

Tests: failed provision row → retry button visible for admin; retry API call re-enters orchestrator and transitions.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_provisioning_retry_api.py -v
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test -- failed-provisions
```

**Commit:** `feat(control-plane): provisioning retry API + admin UI`
<!-- END_TASK_7 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_SUBCOMPONENT_D (tasks 8-10) -->

<!-- START_TASK_8 -->
### Task 8: Nightly Graph sweep — deactivated PI detection

**Verifies:** `rac-v1.AC9.2`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/ownership/graph_sweep.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/ownership/deactivation_logic.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/cli/graph_sweep.py` (pattern: Imperative Shell — entrypoint)
- Create: `infra/modules/graph-sweep-job.bicep` (ACA scheduled job resource)
- Modify: `infra/main.bicep` to invoke the new module
- Create: `apps/control-plane/backend/tests/test_graph_sweep.py`

**Implementation:**

`deactivation_logic.py` (pure):
- `def compute_flagged_apps(apps: list[AppOwnership], graph_results: dict[UUID, GraphUser | None]) -> list[FlaggedApp]`. For each app, if its PI's Graph user is missing or `account_enabled=false`, produce a `FlaggedApp(app_id, pi_principal_id, reason)`. Pure.
- Property test: a PI with `account_enabled=true` never produces a flag; `None` always flags.

`graph_sweep.py` (shell):
1. Query all `app` rows that are `deployed` and not already flagged.
2. Collect unique `pi_principal_id`s.
3. `graph_gateway.get_users_batch(pis)` — batch API lookup (`msgraph-sdk` auto-batches at 20/req).
4. `deactivation_logic.compute_flagged_apps(apps, graph_results)` → list of flags.
5. Insert `app_ownership_flag` rows via Alembic migration `0004_app_ownership_flag.py`. **Design deviation:** the design's data-plane schema does not enumerate `app_ownership_flag` or `app_ownership_flag_review`. Both tables are introduced here to implement AC9.2 while preserving append-only semantics (AC12.1): `app_ownership_flag` is the append-only insert log, and `app_ownership_flag_review` (added in the same migration) holds reviewer decisions without mutating the flag row. This mirrors the `detection_finding` / `detection_finding_decision` pattern in Phase 4. Deviation is approved and documented in `docs/implementation-plans/2026-04-23-rac-v1/README.md`. Columns: `app_ownership_flag`: `id`, `app_id FK`, `pi_principal_id`, `reason`, `flagged_at`, no UPDATE/DELETE grants. `app_ownership_flag_review`: `id`, `flag_id FK`, `review_decision`, `reviewer_principal_id`, `reviewed_at`, no UPDATE/DELETE grants.
6. Log sweep summary.

`cli/graph_sweep.py`: `python -m rac_control_plane.cli.graph_sweep` entrypoint; loads settings, opens DB, runs sweep, exits.

`infra/modules/graph-sweep-job.bicep`: creates `Microsoft.App/jobs` with `triggerType: Schedule`, `cronExpression: "0 2 * * *"` (2 AM UTC), container image = same Control Plane image, command = the CLI entrypoint, managed identity attached (has `User.Read.All` Graph permission).

Tests:
- Active PI → no flag.
- Deactivated PI → flag row inserted.
- Deleted PI (Graph returns None) → flag row with reason `not_found`.
- Re-run sweep when flag already exists → no duplicate insert (idempotent — `UNIQUE(app_id, pi_principal_id, reviewed_at IS NULL)` partial index).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_graph_sweep.py -v
az bicep build --file /home/sysop/rac/infra/modules/graph-sweep-job.bicep
```

**Commit:** `feat(control-plane): nightly Graph sweep for deactivated PIs`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: Ownership transfer + audit preservation

**Verifies:** `rac-v1.AC9.3`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/ownership/transfer.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/ownership.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_ownership_transfer.py`

**Implementation:**

Endpoint `POST /apps/{id}/ownership/transfer`: body `{new_pi_principal_id, new_dept_fallback, justification}`. Admin-only.

Transfer flow:
1. Validate new PI via Graph (`pi_validation`).
2. In a single transaction: UPDATE `app.pi_principal_id`, `app.dept_fallback`; INSERT `approval_event(kind='ownership_transferred', actor_principal_id=admin.oid, submission_id=null, detail={from, to, justification})`.
3. Do NOT touch existing `approval_event` rows — they retain their original `actor_principal_id` (AC9.3).
4. Resolve any open `app_ownership_flag` with `reason='account_disabled'` (now that a new PI is set) — insert a `app_ownership_flag_review` row.

Tests:
- Transfer an app → PI changed, but `SELECT actor_principal_id FROM approval_event WHERE app_id=... AND kind='research_decision'` returns the original approver — unchanged (AC9.3).
- Transfer to a deactivated PI → 422.
- Transfer preserves audit history; if the PI later is deactivated, new flag created.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_ownership_transfer.py -v
```

**Commit:** `feat(control-plane): ownership transfer preserving audit history`
<!-- END_TASK_9 -->

<!-- START_TASK_10 -->
### Task 10: Approval queue UI + admin ownership panel

**Verifies:** `rac-v1.AC2.2` (UI), `rac-v1.AC9.2` (UI), `rac-v1.AC9.3` (UI)

**Files:**
- Create: `apps/control-plane/frontend/src/features/approval-queue/index.tsx`
- Create: `apps/control-plane/frontend/src/features/approval-queue/submission-review.tsx`
- Create: `apps/control-plane/frontend/src/features/admin/ownership/flags-panel.tsx`
- Create: `apps/control-plane/frontend/src/tests/approval-queue.test.tsx`

**Implementation:**

Approval queue:
- Lists submissions in `awaiting_research_review` or `awaiting_it_review` (filtered by the viewer's roles — research approvers see research queue, IT approvers see IT queue, admins see both).
- Detail view shows: submission metadata (researcher, PI, repo, Dockerfile path), scan results (reused from Phase 3), detection findings + decisions (reused from Phase 4), manifest preview (placeholder — fully implemented in Phase 8), and approve/reject/request-changes buttons.
- Scan with `defender_timed_out` shows a prominent "Defender scan pending" badge (AC5.4 UI surfacing).

Admin ownership panel:
- Lists open `app_ownership_flag` rows with PI name (fetched from Graph), app slug, flag reason, and a "Transfer ownership" button that opens the transfer dialog (form populated from Task 9 endpoint).

Tests (vitest):
- Snapshot/interaction test for the queue and the transfer dialog.
- **AC5.4 Defender badge explicit test:** render `<SubmissionReview>` with a fixture where `scan_result.defender_timed_out=true` → assert the badge element with text matching `/Defender scan pending/i` is present in the rendered output. Render the same component with `defender_timed_out=false` → assert the badge is absent. This is the explicit Phase 5 test for AC5.4 UI surfacing.

**Verification:**
```bash
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test -- approval-queue ownership
```

**Commit:** `feat(control-plane): approval queue + ownership admin UI`
<!-- END_TASK_10 -->

<!-- END_SUBCOMPONENT_D -->

<!-- START_TASK_10B -->
### Task 10B: Cost Management export ingestion + per-app cost dashboard

**Verifies:** `rac-v1.AC11.2`, `rac-v1.AC11.3`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/cost/ingest.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/cost/aggregation.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/cost.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/frontend/src/features/admin/cost-dashboard/index.tsx`
- Create: `apps/control-plane/backend/tests/test_cost_ingest.py`
- Create: `apps/control-plane/backend/tests/test_cost_aggregation.py`

**Implementation:**

**Cost export ingestion (Imperative Shell, AC11.2):**

Azure Cost Management can export daily cost data to a Blob Storage container as CSV or JSON. Configure the export in the bootstrap runbook (Tier 1 step — no Bicep because Cost Management exports are subscription-scoped and not fully Bicep-supported). The export lands in the `cost-exports` Blob container (Phase 1 creates this container via `blob-storage.bicep`).

`services/cost/ingest.py`:
1. Triggered nightly by an ACA scheduled job (add `Microsoft.App/jobs` resource in `infra/modules/cost-ingest-job.bicep` — same pattern as `graph-sweep-job.bicep`).
2. List today's cost export files from the `cost-exports` Blob container.
3. For each file: parse the CSV/JSON, group costs by `Tags[rac_app_slug]`. Records without the tag are bucketed as "untagged" and skipped for per-app attribution.
4. Upsert into `cost_snapshot_monthly`: `UPSERT ON CONFLICT (app_slug, year_month) DO UPDATE SET cost_usd = cost_usd + excluded.cost_usd`. The `cost_snapshot_monthly` table (Phase 2 schema migration 0001) already exists.
5. Mark processed blob files with a `processed` tag to prevent double-counting.

`services/cost/aggregation.py` (pure):
```python
# pattern: Functional Core
def compute_cost_summary(snapshots: list[CostSnapshot]) -> CostSummary:
    """Returns per-app month-to-date costs sorted by spend descending."""
    ...

def compute_idle_apps(
    snapshots: list[CostSnapshot],
    app_last_request_at: dict[str, datetime],
    idle_threshold_days: int = 30,
) -> list[IdleApp]:
    """AC11.3: apps with zero cost but min-replicas=0 idle for >= idle_threshold_days."""
    ...
```

**Cost dashboard API + UI (AC11.2, AC11.3):**

`api/routes/cost.py`:
- `GET /admin/cost/summary?year_month=YYYY-MM` → returns `CostSummary` (per-app MTD costs, total, untagged fraction).
- `GET /admin/cost/idle` → returns list of `IdleApp` (app_slug, last_request_at, days_idle, estimated_monthly_savings_usd — estimated as average daily cost × 30 extrapolation). Auth: admin role only.

Frontend cost dashboard:
- `/admin/cost` page: table of per-app MTD spend; bar chart (recharts) showing top 5; "Scale-to-zero savings" section listing idle apps with their idle duration. Filter by month.

Tests:
- `test_cost_ingest.py`: mock blob list + mock CSV → assert correct upserts called (Imperative Shell integration test with mock Azure SDK).
- `test_cost_aggregation.py`: property tests on `compute_cost_summary` (sum is stable, sort is stable) and `compute_idle_apps` (app idle for exactly threshold days is included; app active yesterday is excluded). No mocks — pure functions.

`apps/control-plane/backend/pyproject.toml`: add `azure-storage-blob>=12.23` (if not already added in Phase 1 for other Blob access) and `recharts` to the frontend's `package.json`.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_cost_ingest.py apps/control-plane/backend/tests/test_cost_aggregation.py -v
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test -- cost-dashboard
az bicep build --file /home/sysop/rac/infra/modules/cost-ingest-job.bicep
```

**Commit:** `feat(control-plane): cost ingestion + per-app dashboard (AC11.2, AC11.3)`
<!-- END_TASK_10B -->

<!-- START_TASK_11 -->
### Task 11: End-to-end acceptance — full submission lifecycle

**Verifies:** all Phase 5 ACs (meta)

**Files:** None

**Implementation:**

Against a real dev Azure subscription (with real ACA env, ACR, Key Vault, DNS zone, and Azure Files storage account provisioned via Phase 1):

1. Submit `clean-python-flask` golden repo → pipeline passes (Phase 3) → submission `awaiting_research_review`.
2. Research approver approves → `awaiting_it_review`; `approval_event` has correct actor OID (AC2.2).
3. IT approver approves → provisioning runs; assert via `az containerapp show` that the app exists with correct `env_vars`, `image`, `min_replicas=0`, HTTP scaler, and tags `rac_app_slug`, `rac_pi_principal_id`, `rac_submission_id`, `rac_env`.
4. `az network dns record-set a show` confirms the A record points at the App Gateway IP.
5. `az keyvault key show --name rac-app-<slug>-v1` confirms the signing key exists (AC6.1).
6. `curl -v https://<slug>.${PARENT_DOMAIN}/` — at this phase, the Shim (Phase 6) is not yet deployed, so this will 404 at App Gateway or timeout. Instead, verify internal resolution: `az containerapp exec` into the Control Plane and `curl -v http://<slug>.internal.<env>.azurecontainerapps.io/` — returns 200 from the researcher app (AC6.2 partial — cold-start interstitial is Phase 6).
7. Submit the same slug again with a new commit → `app.current_submission_id` updates atomically; old image (`<slug>:<old_submission_id>`) still exists in ACR (AC6.4).
8. Disable a PI in Entra (test-only) → run `python -m rac_control_plane.cli.graph_sweep` → `app_ownership_flag` row appears (AC9.2).
9. Admin transfers ownership to a new PI → `app.pi_principal_id` updates; `approval_event` history unchanged (AC9.3).
10. Simulate DNS quota exhaustion (patch the SDK client in a test-only injection point or manually delete all DNS zones quota) → provisioning fails; submission stays `approved`; admin UI surfaces retry; retry succeeds after quota restored (AC6.3).

Findings → `phase5-acceptance-report.md`.

**Verification:** commands above on dev subscription.

**Commit:** None.
<!-- END_TASK_11 -->

---

## Phase 5 Done Checklist

- [ ] Azure SDK wrappers all tested with mock SDKs
- [ ] Approval endpoints transition submissions correctly with role checks
- [ ] Provisioning orchestrator creates ACA + DNS + Key + Files atomically with retry
- [ ] All Tier 3 resources carry the four required tags (AC11.1)
- [ ] Re-submission updates `app.current_submission_id` atomically; old image retained in ACR (AC6.4)
- [ ] Graph sweep detects deactivated PIs and inserts flags (AC9.2)
- [ ] Ownership transfer preserves audit history (AC9.3)
- [ ] Defender badge vitest test passes for `defender_timed_out=true` case (AC5.4)
- [ ] `approval_duration_histogram` wired in Task 4 `record.py`; Phase 2 TODO removed (AC10.2 approvals portion)
- [ ] Phase 1 re-deploy loop documented in `bootstrap.md` and Task 1 wired correctly
- [ ] Cost export ingest job + `cost_snapshot_monthly` upserts tested (AC11.2)
- [ ] Idle-app list in cost dashboard shows apps with `min-replicas=0` idle ≥ 30 days (AC11.3)
- [ ] End-to-end lifecycle verified on dev Azure subscription
