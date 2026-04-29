# Pipeline Trust Setup Design

## Summary

RAC's build-and-scan pipeline runs as a GitHub Actions workflow in a separate
repository (`jdkruzr/rac-pipeline`). Today, when the control plane dispatches
a submission into that pipeline, the workflow has no trusted credential for
Azure — it cannot log in to the container registry, read the callback secret
it needs to HMAC-sign its response, or write scan artifacts to storage. This
design plan closes that gap end-to-end.

The approach is layered. On the infrastructure side, a new Bicep module
creates one Entra app registration per environment (`dev`, `staging`,
`prod`), attaches a federated identity credential (FIC) to each, and assigns
the four narrowest Azure roles required — scoped to individual resources,
never to a resource group or subscription. GitHub Actions exchanges its OIDC
token for an Azure access token only when the workflow is running inside the
matching GitHub Environment (`dev`/`staging`/`prod`); the subject string on
the FIC enforces this binding. A second new module creates a dedicated Key
Vault per environment to hold per-submission callback secrets, keeping the
pipeline identity's blast radius strictly separate from the platform KV that
holds TLS certificates and database passwords. On the control-plane side, a
shared `dispatch_for_submission` helper replaces two divergent code paths
(first-create and admin-retry), fixes a placeholder-secret bug that caused
first-time submissions to fail at the pipeline's "Fetch callback secret"
step, and adds a loud 503 response when the GitHub PAT is missing rather
than silently no-op'ing. The combination means a fresh submission dispatched
against the configured dev environment will complete the full build-and-scan
cycle with no manual intervention and no stuck states.

## Definition of Done

1. **Per-environment Entra app registration + federated identity credential**
   for `jdkruzr/rac-pipeline → Azure` trust, codified in a new bicep module.
   Three apps (`rac-pipeline-dev|staging|prod`), each with one FIC trusting
   `repo:jdkruzr/rac-pipeline:environment:<env>`. Parameter-driven so the
   module is multi-env-ready.

2. **Per-resource least-privilege RBAC** assigned by the new module: AcrPush +
   AcrPull on the env's ACR, Key Vault Secrets User on the env's KV, Storage
   Blob Data Contributor on the env's artifacts container — all scoped to the
   per-env resource, never RG- or subscription-wide.

3. **Dev environment actually provisioned and verified**: Entra app + FIC +
   RBAC live in Moffitt's tenant for dev; staging and prod are codified in
   bicep but not deployed in this plan's scope.

4. **GitHub repo `jdkruzr/rac-pipeline` configured (manual + runbook)**: `dev`
   Environment with the three secrets (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`) and five variables (`ACR_NAME`, `ACR_LOGIN_SERVER`,
   `BLOB_ACCOUNT_URL`, `KV_NAME`, `SEVERITY_GATE`). Documented in
   [`docs/runbooks/bootstrap.md`](../runbooks/bootstrap.md#35-pipeline-trust-setup).

5. **`create.py` placeholder-secret bug fixed**: original create-submission
   dispatch path mints the per-submission KV callback secret (same as the
   retry path), so first-time submissions don't fail at the pipeline's "Fetch
   callback secret" step.

6. **Loud-fail on missing PAT**: original create-submission path surfaces a
   503 (or equivalent visible failure) instead of silently no-op'ing, so
   future submissions never get stranded the way #2 and #3 did.

7. **End-to-end verification**: a fresh submission dispatched against the
   configured dev pipeline trust passes Azure login (OIDC), pulls the
   callback secret from KV, and completes through to either
   `awaiting_research_review` or `severity_gate_failed` — no more
   `awaiting_scan` stuck state caused by trust gaps.

**Explicitly out of scope:** GitHub App migration for CP→GH dispatch
(decision #7 in scoping memo, separate plan); staging/prod actual deploys;
logging aggregation; idempotency 5xx semantics fix; Graph 403 mapping.

## Acceptance Criteria

### pipeline-trust.AC1: Per-env identity + FIC provisioned via bicep
- **pipeline-trust.AC1.1 Success:** bicep module deployed with `racEnv='dev'` produces a federated identity credential whose `subject` is exactly `repo:jdkruzr/rac-pipeline:environment:dev`.
- **pipeline-trust.AC1.2 Success:** the same module invoked with `racEnv='staging'` (validated via `az deployment sub validate` + `what-if`, not actually deployed in this plan) produces FIC subject `repo:jdkruzr/rac-pipeline:environment:staging` — proves multi-env-ready.
- **pipeline-trust.AC1.3 Success:** FIC `audiences` is exactly `['api://AzureADTokenExchange']` (no extras, no typos).
- **pipeline-trust.AC1.4 Failure:** bicep `validate` fails with a clear error message when `deployPipelineIdentity=true` AND `pipelineAppObjectId` is empty.
- **pipeline-trust.AC1.5 Edge:** `deployPipelineIdentity=false` (default) produces zero new resources in `what-if` (gate works).

### pipeline-trust.AC2: Per-resource least-privilege RBAC
- **pipeline-trust.AC2.1 Success:** AcrPull + AcrPush role assignments are scoped to the env ACR's `resourceId`, not RG, not subscription.
- **pipeline-trust.AC2.2 Success:** KV Secrets User role assignment is scoped to the env's **pipeline KV** (`kv-rac-pipeline-...`), NOT the platform KV (`kv-rac-...`).
- **pipeline-trust.AC2.3 Success:** Storage Blob Data Contributor is scoped to the artifacts container's `resourceId`, not the storage account.
- **pipeline-trust.AC2.4 Failure:** grep over the deployed bicep `.json` artifacts shows zero role assignments at RG, subscription, or platform-KV scope for the pipeline principal.
- **pipeline-trust.AC2.5 Edge:** when `pipelineAppPrincipalId` is empty, the conditional guards skip role-assignment creation (no empty-principal assignments emitted).

### pipeline-trust.AC3: Dev provisioned; staging/prod codified, not deployed
- **pipeline-trust.AC3.1 Success:** post-Phase-5, `az ad app list --display-name 'rac-pipeline-dev'` returns one app reg; `az role assignment list --assignee <principalId>` shows exactly the four expected assignments.
- **pipeline-trust.AC3.2 Success:** bicepparam wiring for staging and prod compiles and `validate`s cleanly with `deployPipelineIdentity=false` (codified, not deployed).
- **pipeline-trust.AC3.3 Failure:** attempting `az deployment sub create` for staging with `deployPipelineIdentity=true` and empty `pipelineAppObjectIdStaging` fails before any resource is created.

### pipeline-trust.AC4: GitHub repo dev Environment configured per runbook
- **pipeline-trust.AC4.1 Success:** `gh api /repos/jdkruzr/rac-pipeline/environments/dev` returns 200.
- **pipeline-trust.AC4.2 Success:** `gh secret list --env dev --repo jdkruzr/rac-pipeline` shows all three of `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
- **pipeline-trust.AC4.3 Success:** `gh variable list --env dev --repo jdkruzr/rac-pipeline` shows all five of `ACR_NAME`, `ACR_LOGIN_SERVER`, `BLOB_ACCOUNT_URL`, `KV_NAME`, `SEVERITY_GATE`.
- **pipeline-trust.AC4.4 Success:** the value of `KV_NAME` equals the pipeline KV's name (`kv-rac-pipeline-...`), NOT the platform KV's name.
- **pipeline-trust.AC4.5 Success:** `docs/runbooks/bootstrap.md` contains a "Pipeline Trust Setup" section with the manual app-reg + GH-Environment + bicepparam + verification steps.

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

### pipeline-trust.AC7: End-to-end verification on live dev
- **pipeline-trust.AC7.1 Success:** a previously-stuck submission re-dispatched via `POST /api/admin/submissions/{id}/dispatch/retry` produces a GH Actions workflow run where the `Azure login (OIDC)` step exits 0 (was failing before this plan).
- **pipeline-trust.AC7.2 Success:** the same workflow's `Fetch callback secret` step successfully reads `rac-pipeline-cb-{submission_id}` from `kv-rac-pipeline-dev`.
- **pipeline-trust.AC7.3 Success:** the workflow completes build+scan, POSTs an HMAC-signed callback to the control plane, and the callback is accepted (HTTP 200).
- **pipeline-trust.AC7.4 Success:** the submission FSM advances out of `awaiting_scan` to either `awaiting_research_review` or `severity_gate_failed` (depending on the test image's scan verdict).
- **pipeline-trust.AC7.5 Failure:** a fresh submission attempted while the live control plane has `RAC_GH_PAT` unset returns 503 (in-prod replication of AC6.1).

## Glossary

- **Entra app registration**: An Azure Entra ID (formerly Azure Active
  Directory) identity record that represents an application. Provides a
  client ID and can hold credentials or federated identity credentials. In
  this plan, one registration is created per pipeline environment.
- **Federated identity credential (FIC)**: A trust record attached to an
  Entra app registration that allows an external identity provider's token
  (here, a GitHub Actions OIDC token) to be exchanged for an Azure access
  token — no stored secret required.
- **OIDC (OpenID Connect)**: An identity protocol used here by GitHub
  Actions to issue short-lived, signed tokens that describe what workflow
  is running. Azure's FIC mechanism validates these tokens to grant Azure
  access without a long-lived credential.
- **`sub` claim / FIC subject**: The field in the OIDC token that GitHub
  Actions sets to a string like `repo:jdkruzr/rac-pipeline:environment:dev`.
  The FIC on the Entra app must match this string exactly; a mismatch
  causes the token exchange to fail.
- **GitHub Environment**: A named deployment context in a GitHub Actions
  workflow (e.g., `dev`, `staging`, `prod`). Declaring `environment: dev`
  on a job controls which secrets and variables are injected and determines
  the `sub` claim value in the OIDC token.
- **`repository_dispatch`**: A GitHub Actions trigger that allows an
  external system (here, the RAC control plane) to fire a workflow run via
  the GitHub REST API. The control plane POSTs to this endpoint using a
  Personal Access Token.
- **RBAC role assignment**: An Azure authorization record granting a
  principal (here, the pipeline's Entra service principal) a specific role
  on a specific resource. This plan uses resource-scoped assignments rather
  than broader resource-group or subscription scope.
- **AcrPull / AcrPush**: Built-in Azure role definitions for Azure
  Container Registry. AcrPull allows reading images; AcrPush allows
  writing (pushing) images. Both are required for the pipeline to pull a
  base image and push the built researcher image.
- **Key Vault Secrets User**: A built-in Azure RBAC role that grants
  read-only access to Key Vault secrets. Assigned to the pipeline identity
  on the pipeline KV only, not the platform KV.
- **Storage Blob Data Contributor**: A built-in Azure RBAC role granting
  read/write/delete on blob storage. Assigned at the individual container
  level so the pipeline can write scan artifacts without access to other
  containers.
- **Platform KV vs. pipeline KV**: The platform KV (`kv-rac-...`) holds
  infrastructure secrets — database passwords, TLS certificates, JWS
  signing keys. The pipeline KV (`kv-rac-pipeline-...`) holds only
  per-submission HMAC callback secrets. Keeping them separate limits what
  a compromised pipeline workflow can access.
- **HMAC callback secret**: A randomly generated secret stored in the
  pipeline KV at submission dispatch time. The pipeline uses it to sign
  its callback POST to the control plane; the control plane verifies the
  signature to authenticate the result. Per-submission, not shared.
- **`mint_callback_secret`**: The control-plane function that generates a
  callback secret and writes it to the pipeline KV with an expiry of
  `2 × pipeline_timeout_minutes`.
- **`dispatch_for_submission`**: The new shared helper in
  `services/pipeline_dispatch/dispatch_helper.py` that consolidates
  submission dispatch logic — minting the callback secret, building the
  payload, and POSTing to GitHub — for both first-create and admin-retry
  code paths.
- **`DispatchUnavailableError`**: A new exception raised by
  `dispatch_for_submission` when the GitHub PAT (`RAC_GH_PAT`) is absent.
  Mapped to a 503 response at the API layer.
- **Functional Core / Imperative Shell (FCIS)**: An architectural
  discipline used throughout the RAC codebase. Pure functions with no I/O
  or side effects carry the `# pattern: Functional Core` marker; modules
  that perform I/O (DB writes, HTTP calls, KV access) carry
  `# pattern: Imperative Shell`. The new `dispatch_helper.py` is
  Imperative Shell.
- **Two-pass deploy gate**: A Bicep pattern used in this codebase where
  new infrastructure is introduced behind a boolean parameter that
  defaults to `false`. Pass 1 deploys with the gate off (no change); after
  manual prerequisites are satisfied, Pass 2 flips the gate on.
- **`racEnv`**: The canonical environment selector string (`dev`,
  `staging`, or `prod`) threaded through all Bicep modules. Determines
  resource names, FIC subject strings, and parameter file selection.
- **`uniqueString()`**: A Bicep built-in function that derives a
  deterministic short hash from one or more input strings (typically the
  resource group ID). Used in resource names to avoid global-namespace
  collisions while staying stable across redeployments.
- **Syft / Grype**: Open-source tools used in the pipeline for software
  composition analysis. Syft generates a software bill of materials
  (SBOM); Grype scans that SBOM for known vulnerabilities. Their JSON
  outputs are uploaded to the artifacts storage container.
- **`SEVERITY_GATE`**: A GitHub Actions variable controlling the minimum
  vulnerability severity level that causes the pipeline to fail the scan
  step and advance the submission to `severity_gate_failed` rather than
  `awaiting_research_review`.
- **`awaiting_scan` stuck state**: The submission FSM state a submission
  enters after dispatch. Before this plan, submissions remained stuck here
  indefinitely because the pipeline could not authenticate to Azure and
  never POSTed a callback.
- **FSM (finite state machine)**: The submission status model in the
  control plane. A submission moves through states (`awaiting_scan` →
  `awaiting_research_review` or `severity_gate_failed`, etc.) as pipeline
  events arrive.
- **`AADSTS70021` / `AADSTS700213`**: Azure AD error codes returned when
  an OIDC token exchange fails because no matching FIC was found
  (`70021`) or the FIC subject does not match (`700213`). Cited as the
  observable failure mode for a misconfigured FIC.

## Architecture

The build-and-scan pipeline (`jdkruzr/rac-pipeline`) authenticates to a
RAC tenant's Azure subscription via GitHub Actions OIDC + per-environment
federated identity credentials on per-environment Entra app registrations.
The control plane dispatches the pipeline via `repository_dispatch` (PAT
auth, unchanged) and the pipeline calls back via HMAC-signed webhook using
a per-submission callback secret stored in a dedicated callback-secrets
Key Vault.

**Trust topology (per environment, e.g. dev):**

- One Entra app registration `rac-pipeline-dev` with one federated identity
  credential whose subject is `repo:jdkruzr/rac-pipeline:environment:dev`.
- The same trust shape repeats for `staging` and `prod`. Within Moffitt's
  single Entra tenant, three separate app registrations isolate RBAC blast
  radius — a misconfigured FIC subject on the dev app cannot grant access
  to prod resources because the dev app holds no roles on prod.
- The federated credential is matched only when the workflow is triggered
  AND declares `environment: dev` (or staging/prod) on the build job. This
  combination is documented and verified for `repository_dispatch` triggers
  per the GitHub OIDC reference.

**RBAC topology (per environment):**

The per-env Entra app's principal is granted four role assignments, each
scoped at the resource level (never RG, never subscription):

| Role | Scope | Why |
|------|-------|-----|
| AcrPull (`7f951dda-...`) | env ACR | `azure/login@v2` flow + Docker manifest read |
| AcrPush (`8311e382-...`) | env ACR | push researcher image with `${slug}:${submission_id}` |
| Key Vault Secrets User (`4633458b-...`) | env **pipeline** KV (NOT platform KV) | read `rac-pipeline-cb-${submission_id}` |
| Storage Blob Data Contributor (`ba92f5b4-...`) | env artifacts container only | upload Syft SBOM + Grype JSON + scan logs |

**Data flow at dispatch (control plane → GitHub):** unchanged from today's
PAT-based path, except the callback_secret_name now references the
dedicated pipeline KV. POST to
`https://api.github.com/repos/jdkruzr/rac-pipeline/dispatches` with
`event_type: rac_submission` and `client_payload` containing
`callback_secret_name: rac-pipeline-cb-{submission_id}`.

**Data flow at pipeline runtime:**

1. Workflow run loads its declared GH Environment (`dev`/`staging`/`prod`),
   getting `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
   secrets and `ACR_NAME`, `ACR_LOGIN_SERVER`, `BLOB_ACCOUNT_URL`,
   `KV_NAME`, `SEVERITY_GATE` variables.
2. `azure/login@v2` exchanges the workflow's OIDC token (sub claim:
   `repo:jdkruzr/rac-pipeline:environment:dev`) against the dev app's
   FIC, receiving an access token scoped to dev resources only.
3. `az acr login` succeeds against dev ACR (AcrPull/AcrPush).
4. `az keyvault secret show --vault-name kv-rac-pipeline-dev --name
   rac-pipeline-cb-${submission_id}` succeeds (KV Secrets User on
   the pipeline KV).
5. Build → scan → upload artifacts to dev storage `scan-artifacts`
   container.
6. Pipeline POSTs HMAC-signed callback to the control plane.

**Per-env divergence is parameter-driven only.** The bicep modules are
identical across envs; staging/prod bring-up is a re-run of the runbook
against staging/prod params. Only dev is provisioned in this plan's scope.

**Callback-secret storage isolation.** The pipeline identity holds Secrets
User on its env's dedicated `kv-rac-pipeline-${racEnv}` only. The platform
KV (`kv-rac-${uniqueString}-${racEnv}`) holds runtime secrets like
`rac-pg-admin-password`, App Gateway TLS, JWS signing keys — none of which
are accessible to the pipeline identity. A compromised pipeline workflow
gets per-submission HMAC secrets and write access to one storage container;
nothing more.

**Module boundary contracts.**

`infra/modules/pipeline-identity.bicep`:
```bicep
// Inputs
param racEnv string
param pipelineAppObjectId string       // manual app reg's `id`
param pipelineAppPrincipalId string    // enterprise-app principal ID
param pipelineGithubOwner string
param pipelineGithubRepo string
param acrId string
param pipelineKvId string
param artifactsBlobContainerId string

// Resources created
//  - Microsoft.Graph/applications/federatedIdentityCredentials@v1.0
//      subject: 'repo:${owner}/${repo}:environment:${racEnv}'
//      audiences: ['api://AzureADTokenExchange']
//  - Microsoft.Authorization/roleAssignments@2022-04-01 × 4
//      each at resource scope, principalType: 'ServicePrincipal'

// Outputs
output ficResourceId string
output roleAssignmentIds array
```

`infra/modules/pipeline-kv.bicep`:
```bicep
// Inputs
param racEnv string
param location string
param tags object
param controlPlaneMiPrincipalId string

// Resources
//  - Microsoft.KeyVault/vaults: 'kv-rac-pipeline-${uniqueString(...)}-${racEnv}'
//      enableRbacAuthorization: true
//      softDeleteRetentionInDays: 7
//      purgeProtection: false (dev; param for staging/prod)
//  - roleAssignment: CP MI -> Key Vault Secrets Officer

// Outputs
output kvId string
output kvUri string
output kvName string
```

`services/pipeline_dispatch/dispatch_helper.py` (new shared helper, replaces
the divergent code paths in `create.py` and `retry.py`):
```python
async def dispatch_for_submission(
    submission: Submission,
    *,
    settings: Settings,
    triggered_by: str,  # 'submission_created' | 'admin_retry'
) -> dict[str, str]:
    """Mint callback secret, build payload, POST repository_dispatch.

    Raises DispatchUnavailableError when settings.gh_pat is unset.
    Returns dict: submission_id, callback_url, dispatched_at (ISO 8601).
    """
```

## Existing Patterns

This design follows established patterns in the codebase, with one
deliberate divergence.

**Followed patterns:**

- **Per-env parameterization.** `racEnv` (string `'dev'|'staging'|'prod'`)
  is the canonical env selector across the bicep tree. Naming convention
  `<prefix>-rac-<purpose>-${racEnv}` (e.g. `id-rac-controlplane-dev`,
  `kv-rac-pipeline-${uniqueString(...)}-dev`). Reference module:
  `infra/modules/managed-identity.bicep`.
- **Role-ID centralization.** All built-in role GUIDs come from
  `infra/modules/role-ids.bicep`, never inline. New file extension adds
  `acrPush` and `storageBlobDataContributor` constants alongside existing
  `acrPull`, `keyVaultSecretsUser`, etc. Ref: `infra/modules/role-ids.bicep`,
  invariant documented in `infra/CLAUDE.md`.
- **Role-assignment shape.** `Microsoft.Authorization/roleAssignments@2022-04-01`
  with `name: guid(scope.id, principalId, roleId)`,
  `principalType: 'ServicePrincipal'`, conditional
  `if (!empty(principalId))`. Ref: `infra/modules/role-assignments.bicep`.
- **Two-pass-deploy gating.** New work goes behind a boolean param
  defaulting `false` (`deployPipelineIdentity bool = false`). Pass 1
  leaves it off; manual app reg + bicepparam edit; Pass 2 flips it on.
  Ref: existing `deployTelemetryAlerts` and similar gates in
  `infra/main.bicep`.
- **Functional Core / Imperative Shell.** New `dispatch_helper.py` carries
  `# pattern: Imperative Shell` (it does I/O). The pure
  `build_dispatch_payload` (`# pattern: Functional Core`) is unchanged.
  Ref: invariants in `apps/control-plane/backend/CLAUDE.md`.
- **Loud-fail on misconfiguration.** `DispatchUnavailableError` →
  `ServiceUnavailableError` → 503. Mirrors the retry-endpoint pattern
  added today (`services/pipeline_dispatch/retry.py`,
  `tests/test_dispatch_retry_api.py::test_retry_dispatch_no_auth_token_503`).
- **GitHub Environments + per-env FIC subjects.** Identical pattern to
  the existing rac-infra-deploy SP: bootstrap.md §3 already documents
  `repo:<ORG>/rac:environment:dev|staging|prod` for the *infra deploy*
  app; this design extends the same pattern to the *pipeline*
  app registrations.

**Deliberate divergence — first codebase use of `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0`:**

All three existing app registrations (CP-OIDC, CP-API, RAC-Infra-Deploy)
are created manually via az CLI. This design also creates the new
`rac-pipeline-${env}` app registrations manually (matching the existing
pattern), but the FIC under each app is provisioned via bicep. Justified
by: FIC is the part most prone to drift when edited by hand; codifying it
in bicep ensures the subject string can never silently diverge from
`racEnv`. The deploy SP needs to be added as Owner of each new app reg
(documented in runbook) so bicep can manage child resources.

**No existing pattern for:**

- Dedicated callback-secrets KV per env. The codebase has one platform
  KV today; introducing a second per-env KV for narrow blast radius is
  new. Justified by Azure RBAC's vault-scoped (not secret-name-scoped)
  granularity.

## Implementation Phases

<!-- START_PHASE_1 -->
### Phase 1: Bicep groundwork (modules, no infra change)
**Goal:** Create the two new modules and extend role IDs; nothing wired into main.bicep yet.

**Components:**
- `infra/modules/role-ids.bicep` — extend with `acrPush` (`8311e382-0749-4cb8-b61a-304f252e45ec`) and `storageBlobDataContributor` (`ba92f5b4-2d11-453d-a403-e96b0029c9fe`) GUID constants.
- `infra/modules/pipeline-kv.bicep` — new module; creates dedicated callback-secrets KV per env, RBAC-mode, soft-delete 7 days, no purge protection (dev). Grants CP MI Key Vault Secrets Officer.
- `infra/modules/pipeline-identity.bicep` — new module; creates one `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0` under the existing app reg + four `Microsoft.Authorization/roleAssignments@2022-04-01` (AcrPull, AcrPush, KV Secrets User on pipeline KV, Storage Blob Data Contributor on artifacts container).

**Dependencies:** None.

**Done when:** `az bicep build` on each module produces zero warnings; generated `.json` siblings committed.
<!-- END_PHASE_1 -->

<!-- START_PHASE_2 -->
### Phase 2: Bicep integration (gated, no infra change yet)
**Goal:** Wire the new modules into main.bicep behind a default-off gate; what-if shows zero diff.

**Components:**
- `infra/main.bicep` — add params `deployPipelineIdentity bool = false`, `pipelineAppObjectIdDev string = ''`, `pipelineAppPrincipalIdDev string = ''`, `pipelineAppClientIdDev string = ''` (output for runbook reference). Conditional invocations of `pipeline-kv` and `pipeline-identity` modules.
- `infra/main.dev.bicepparam` — leave new params empty for now; document in comments that they get populated after manual app reg.

**Dependencies:** Phase 1.

**Done when:** `az deployment sub validate` passes both with gate off (default) and gate on with stub principalIds; `az deployment sub what-if` against the live dev RG shows an empty diff with gate off.
<!-- END_PHASE_2 -->

<!-- START_PHASE_3 -->
### Phase 3: Control plane — shared dispatch helper, fix create.py, loud-fail, KV-URI plumbing
**Goal:** Eliminate the placeholder-secret bug, route all dispatch through one helper, and surface 503 on missing PAT. Wire pipeline KV URI through Settings.

**Components:**
- `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/dispatch_helper.py` — new module (`# pattern: Imperative Shell`); contract per Architecture section.
- `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py` — refactor to call `dispatch_for_submission`; preserve the audit-log line tagged with `admin_oid`.
- `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py` — replace the placeholder-secret payload construction (~line 200) with a call to `dispatch_for_submission`; remove silent-skip branch.
- `apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py` — catch `DispatchUnavailableError` from create flow and map to 503 (mirrors provisioning.py's retry route).
- `apps/control-plane/backend/src/rac_control_plane/settings.py` — add `pipeline_kv_uri: str` (env var `RAC_PIPELINE_KV_URI`).
- `infra/modules/control-plane-aca-app.bicep` — set `RAC_PIPELINE_KV_URI` env var on the CP container app, sourced from the `pipeline-kv` module output.

**Acceptance Criteria covered:** pipeline-trust.AC5, pipeline-trust.AC6.

**Dependencies:** Phase 2 (the bicep that emits the new env var must compile, even if not yet deployed).

**Done when:** new tests in `tests/test_pipeline_dispatch.py` and `tests/test_submissions_api.py` (per §6 Layer 1) pass; full backend suite (656+ tests) green; `_build_dispatch_fn`'s silent-no-op branch is gone; type checker clean.
<!-- END_PHASE_3 -->

<!-- START_PHASE_4 -->
### Phase 4: Runbook & docs (no code change)
**Goal:** A fresh operator can go from "no app reg" to "successful test dispatch" without external help.

**Components:**
- `docs/runbooks/bootstrap.md` — new section "Pipeline Trust Setup" placed after §3 (Federated Identity Credentials for GHA). Covers: (a) `az ad app create` per env, capture object/principal/client IDs, (b) `az ad app owner add` to grant deploy SP management of each new app reg, (c) GitHub Environment configuration (3 secrets, 5 variables) with the pipeline KV name as `KV_NAME` (NOT platform KV), (d) bicepparam update with captured IDs, (e) Pass 2 deploy with `deployPipelineIdentity=true`, (f) 5-minute RBAC-propagation wait, (g) verification checklist (admin retry + happy-path submission).
- `apps/control-plane/backend/CLAUDE.md` — add a one-paragraph note on the pipeline-identity contract: "Per-env pipeline app reg holds RBAC on its env's resources only; callback secrets live in `kv-rac-pipeline-${env}`, never in the platform KV; `dispatch_for_submission` is the single entry point."

**Dependencies:** None (can proceed in parallel with Phases 1–3).

**Done when:** Both files updated; `gh markdown-render` (or equivalent) shows clean rendering; cross-references between bootstrap.md and the design plan are bidirectional.
<!-- END_PHASE_4 -->

<!-- START_PHASE_5 -->
### Phase 5: Dev deploy and end-to-end verification
**Goal:** Prove the trust setup works against a live RAC dev deploy and a previously-stranded submission.

**Components:**
- Operational execution of the runbook against `rg-rac-dev` in the live Moffitt subscription.
- Manual app reg `rac-pipeline-dev` created in Moffitt's Entra tenant.
- GitHub Environment `dev` configured at `jdkruzr/rac-pipeline`.
- Pass 2 bicep deploy with `deployPipelineIdentity=true` and captured IDs.
- Test 1 (happy path): admin POST `/api/admin/submissions/{id}/dispatch/retry` against a stuck submission → confirm in GH Actions that `Azure login (OIDC)` step passes, `Fetch callback secret` succeeds, build+scan completes, callback received by control plane, submission FSM advances out of `awaiting_scan`.
- Test 2 (loud-fail): redeploy the control plane container app with `RAC_GH_PAT` env var deliberately unset → submit a new submission → confirm 503 returned, no orphan submission row in DB.

**Acceptance Criteria covered:** pipeline-trust.AC1, pipeline-trust.AC2, pipeline-trust.AC3, pipeline-trust.AC4, pipeline-trust.AC7.

**Dependencies:** Phases 1–4.

**Done when:** at least one previously-stuck submission completes through the build-and-scan pipeline AND the misconfigured-PAT case returns a clean 503 with no DB side-effects.
<!-- END_PHASE_5 -->

## Additional Considerations

**Error handling and edge cases:**

- **OIDC token exchange failure (FIC mismatch):** `azure/login@v2` fails with `AADSTS70021` or `AADSTS700213` if the FIC subject doesn't match the workflow's `environment:` declaration. Mitigation: bicep hardcodes `audiences: ['api://AzureADTokenExchange']` and derives the FIC subject from the same `racEnv` param that drives every other env decision; structurally hard to drift.
- **RBAC propagation delay:** Azure RBAC is eventually consistent (~5 min). Runbook explicitly waits between deploy completion and the first test dispatch.
- **Callback-secret expiration:** `mint_callback_secret` sets expiry to `2 × pipeline_timeout_minutes` (currently 240 min). If a queued workflow is delayed beyond that, the secret expires. Acceptable for now; bumping `pipeline_timeout_minutes` is the lever.
- **KV soft-delete pile-up in dev:** every submission mints a new secret. `softDeleteRetentionInDays: 7` (Azure minimum) and `purgeProtection: false` for dev keep churn manageable. Staging/prod can override.
- **Duplicate dispatch race:** if admin retry is invoked twice for the same submission, the second `mint_callback_secret` overwrites the first; the first pipeline run's HMAC will mismatch its callback. Documented in `dispatch_helper.py` module header. Acceptable for now (admin operation, low frequency).
- **Concurrency on platform KV:** unaffected. Pipeline never touches it.

**Idempotency:** all bicep resources are idempotent by construction. FIC has fixed `name` per env, KV has fixed name, role assignments use deterministic `guid()` names. Re-running the deploy after partial failure converges.

**Out of scope, flagged for follow-up plans:**

- **GitHub App migration for CP→GH dispatch.** Today's PAT is tied to a single human's GitHub account. A GitHub App with installation tokens is more rotation-friendly. Decision #7 in the scoping memo; separate plan.
- **Stuck-pipeline alert.** A dispatch can succeed (204) but the workflow may not actually trigger or may fail silently. Operational alerting is followup #4 in `project_rac_pending_followups.md`; separate plan.
- **Logging aggregation.** Followup #9; separate plan.
- **Staging/prod actual deploys.** Same runbook re-run against staging/prod params. Not in this plan's DoD.

**Future extensibility:** the per-env Entra-app + FIC + RBAC pattern established here applies cleanly to any additional GH-side service that later needs Azure access (e.g. a docs publisher, an issue-triage bot). The `pipeline-identity.bicep` module is named for clarity but the shape is reusable — a future generalization could rename it to `github-actions-identity.bicep` if more callers emerge.
