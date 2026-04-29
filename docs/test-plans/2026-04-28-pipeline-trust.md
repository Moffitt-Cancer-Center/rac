# Pipeline-Trust Human Test Plan

Generated 2026-04-29 from `docs/implementation-plans/2026-04-28-pipeline-trust/test-requirements.md` after automated coverage validation passed (13/13 phases-1-4 criteria covered).

This plan covers the manual verification steps a human operator runs to validate Phases 1–5 of the pipeline-trust implementation against the live `rg-rac-dev` resource group and `jdkruzr/rac-pipeline` GitHub repo. The automated tests already pinned the bicep structure and control-plane behavior; this plan validates the runtime topology after Pass-2 deploy.

## Prerequisites

- Operator has `az` CLI signed in to the Moffitt subscription with rights to `rg-rac-dev`.
- Operator has `gh` CLI signed in to GitHub with admin access to `jdkruzr/rac-pipeline`.
- Pass-1 platform deploy is complete (`rg-rac-dev` exists with platform KV, ACR, storage, ACA env).
- Phase-5 operator runbook variables exported (per `docs/runbooks/bootstrap.md` §3.5):
  - `PIPELINE_APP_OBJECT_ID`, `SP_OBJECT_ID`, `CONTROL_PLANE_HOST`, `ADMIN_TOKEN`.
- Backend tests passing locally: `cd apps/control-plane/backend && uv run pytest` reports 670+ passing.
- Working session log being captured to `/tmp/phase5-deploy.log` (per phase_05.md instructions); attach to the implementation-plan PR after completion.

## Phase A: Live Trust Topology (Bicep + Entra)

| Step | Action | Expected |
|------|--------|----------|
| A1 (AC3.1) | `az ad app list --display-name 'rac-pipeline-dev' --query "length(@)" -o tsv` | Output: `1` |
| A2 (AC3.1) | `az role assignment list --assignee "$SP_OBJECT_ID" --query "length(@)" -o tsv` | Output: `4` (AcrPull, AcrPush, KV Secrets User, Storage Blob Data Contributor) |
| A3 (AC1.1) | `az ad app federated-credential list --id "$PIPELINE_APP_OBJECT_ID" --query "[?name=='rac-pipeline-dev-gha'].subject" -o tsv` | Output exactly: `repo:jdkruzr/rac-pipeline:environment:dev` |
| A4 (AC1.2) | Inspect `infra/modules/pipeline-identity.json` rendered FIC subject and confirm it uses `[parameters('controlPlaneGithubPipelineOwner')]`, `[parameters('controlPlaneGithubPipelineRepo')]`, `[parameters('racEnv')]` placeholders. Then `az deployment sub what-if --parameters infra/environments/staging.bicepparam ...` (with stub principal IDs if necessary) | Subject template is parameter-driven; `racEnv='staging'` would yield `environment:staging`. |
| A5 (AC3.3) | `az deployment sub validate --parameters deployPipelineIdentity=true pipelineAppPrincipalIdDev=''` (or `sub create` if operator chooses) against staging-shaped param file | Non-zero exit; stderr mentions "name property required" or equivalent ARM template-validation error. **No resources created.** |

## Phase B: Per-Resource Least-Privilege Scope Inspection

| Step | Action | Expected |
|------|--------|----------|
| B1 (AC2.1) | `az role assignment list --assignee "$SP_OBJECT_ID" --query "[].{role:roleDefinitionName,scope:scope}" -o json \| jq '[.[] \| select(.role == "AcrPull" or .role == "AcrPush")]'` | Exactly 2 entries; both `scope` fields end in `/Microsoft.ContainerRegistry/registries/<acr-name>` (NOT RG, NOT subscription). |
| B2 (AC2.2) | Same `az role assignment list` filtered to `Key Vault Secrets User` | Exactly 1 entry; `scope` ends in `/Microsoft.KeyVault/vaults/kv-rac-pl-...`. **Critical:** the prefix MUST be `kv-rac-pl-` (pipeline KV), NOT just `kv-rac-` (platform KV). Mis-scoping here breaks the blast-radius isolation. |
| B3 (AC2.3) | Same `az role assignment list` filtered to `Storage Blob Data Contributor` | Exactly 1 entry; `scope` path includes `/Microsoft.Storage/.../containers/scan-artifacts` (NOT `/storageAccounts/<x>` alone). |

## Phase C: GitHub Environment & OIDC Wiring

| Step | Action | Expected |
|------|--------|----------|
| C1 (AC4.1) | `gh api /repos/jdkruzr/rac-pipeline/environments/dev --jq '.name'` | Output: `dev`; HTTP 200. |
| C2 (AC4.2) | `gh secret list --env dev --repo jdkruzr/rac-pipeline \| awk '{print $1}'` | Output includes all of: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. |
| C3 (AC4.3) | `gh variable list --env dev --repo jdkruzr/rac-pipeline \| awk '{print $1}'` | Output includes all of: `ACR_NAME`, `ACR_LOGIN_SERVER`, `BLOB_ACCOUNT_URL`, `KV_NAME`, `SEVERITY_GATE`. |
| C4 (AC4.4) | `gh variable get KV_NAME --env dev --repo jdkruzr/rac-pipeline` | Value starts with `kv-rac-pl-` (NOT just `kv-rac-`). This is the pipeline KV, not the platform KV. |

## Phase D: End-to-End Pipeline Run with OIDC Trust

Purpose: validate the full OIDC token-exchange → pipeline-KV read → callback path against the live deployment. This is the keystone test — every prior phase only proves a static state; this one proves the trust topology actually works at runtime.

| Step | Action | Expected |
|------|--------|----------|
| D1 (AC7.1) | Trigger admin retry on a stuck submission (per phase_05.md Task 6 / runbook §3.5.8). In the GitHub Actions UI for `jdkruzr/rac-pipeline`, click the workflow run; click the "Azure login (OIDC)" step. | Step status green; log shows `Login successful` or equivalent OIDC token-exchange success message. |
| D2 (AC7.2) | In the same workflow run, click the "Fetch callback secret" step. | Step exits 0; log line cites the secret name `rac-pipeline-cb-${SUBMISSION_ID}` and a successful `az keyvault secret show` against `kv-rac-pl-...-dev` (NOT platform KV). |
| D3 (AC7.3) | `az containerapp logs show --name 'ca-rac-controlplane-dev' --resource-group rg-rac-dev --tail 100 \| grep -E "callback_received\|callback_signature_verified\|callback_accepted"` | At least one match for the test submission_id. Workflow run status in GH UI is `success`. |
| D4 (AC7.4) | `curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "${CONTROL_PLANE_HOST}/api/submissions/${SUBMISSION_ID}" \| jq '.status'` | Status is `awaiting_research_review` or `severity_gate_failed`. **NOT** `awaiting_scan` (which would mean callback wasn't processed). |

## Phase E: Loud-Fail in Production (AC7.5 Replication of AC6.1)

Purpose: AC6.1 is verified at unit/integration level; AC7.5 verifies the same loud-fail behavior against the deployed control plane.

| Step | Action | Expected |
|------|--------|----------|
| E1 | `az containerapp update --name ca-rac-controlplane-dev --resource-group rg-rac-dev --remove-env-vars RAC_GH_PAT` | Successful update; new revision activates. |
| E2 | Wait for new revision to be ready (~1–2 min). |  |
| E3 | `curl -i -X POST "${CONTROL_PLANE_HOST}/api/submissions" -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{...valid submission body...}'` | Response status `503`; body contains `"code":"service_unavailable"` and the string `RAC_GH_PAT`; correlation_id present. |
| E4 | `psql ... -c "SELECT count(*) FROM submission WHERE created_at > <test-window-start>;"` | Count = 0 (no orphan DB row). |
| E5 (CLEANUP — MANDATORY) | `az containerapp update --name ca-rac-controlplane-dev --resource-group rg-rac-dev --set-env-vars "RAC_GH_PAT=secretref:rac-gh-pat"` (or whatever the original secretref was) | Env var restored; submissions API resumes accepting requests. Verify with one fresh successful POST. |

## End-to-End: First-Submission-After-Trust Setup

Purpose: validate that a brand-new researcher submission flows through dispatch end-to-end against the just-bootstrapped trust setup, exercising AC1.1, AC2.1–2.3, AC4.1–4.4, AC7.1–7.4 in one continuous trace.

Steps:
1. As an authenticated researcher (test user), use the SPA at `${CONTROL_PLANE_HOST}/` to submit a small public-data app (e.g., the example container in fixtures).
2. Confirm the SPA shows a submission ID and a status of `awaiting_scan`.
3. Open `https://github.com/jdkruzr/rac-pipeline/actions` and watch for a new workflow run triggered by the dispatch.
4. Walk through workflow steps; confirm OIDC login, KV fetch, build, scan, and callback all succeed.
5. Re-fetch `${CONTROL_PLANE_HOST}/api/submissions/${SUBMISSION_ID}` and confirm status advanced to `awaiting_research_review` or `severity_gate_failed`.
6. Confirm callback log entries appear in control-plane logs.

This single trace exercises the trust topology, the dispatch helper, the per-submission HMAC secret minting, the pipeline-KV scope, the OIDC token exchange, and the HMAC-signed callback path — all the pieces phase_05 is designed to verify.

## Human Verification Required

| Criterion | Why Manual | Steps |
|-----------|------------|-------|
| AC1.1 | Requires real Microsoft Graph state in Moffitt tenant after Pass-2 deploy. | Phase A, Step A3. |
| AC1.2 | Requires deploy-time parameter resolution; staging param file may not have real principal IDs. | Phase A, Step A4. |
| AC2.1 | Requires reading deployed Azure RBAC role-assignment records on the live ACR. | Phase B, Step B1. |
| AC2.2 | Requires inspecting live KV scope; enforces platform-KV vs pipeline-KV blast-radius separation. | Phase B, Step B2. |
| AC2.3 | Requires reading deployed container-level scope on the live storage account. | Phase B, Step B3. |
| AC3.1 | Requires Microsoft Graph for post-deploy app-reg state. | Phase A, Steps A1–A2. |
| AC3.3 | Requires `az deployment sub validate` against staging-shaped param file with gate on, principal empty. | Phase A, Step A5. |
| AC4.1 | GH Environment is created manually per runbook §3.5.3. | Phase C, Step C1. |
| AC4.2 | Requires inspecting live GH repo state. | Phase C, Step C2. |
| AC4.3 | Requires inspecting live GH repo state. | Phase C, Step C3. |
| AC4.4 | String-equality check against deployed pipeline KV name. | Phase C, Step C4. |
| AC7.1 | Requires GH Actions OIDC token exchange against Azure Entra ID — cannot mock without losing test value. | Phase D, Step D1. |
| AC7.2 | Live workflow reads from live pipeline KV using OIDC-issued token. | Phase D, Step D2. |
| AC7.3 | End-to-end through HMAC-signed callback to the deployed control plane. | Phase D, Step D3. |
| AC7.4 | Depends on full pipeline run completing and a real callback being processed. | Phase D, Step D4. |
| AC7.5 | Requires actually redeploying live control plane with `RAC_GH_PAT` removed from env block. | Phase E, Steps E1–E5 (cleanup mandatory). |

## Traceability

| Acceptance Criterion | Automated Test | Manual Step |
|----------------------|----------------|-------------|
| AC1.1 | — | A3 |
| AC1.2 | — | A4 |
| AC1.3 | grep over `pipeline-identity.bicep` + `.json` | — |
| AC1.4 | `phase_02.md` Task 4 CLI: `az deployment sub validate` w/ empty principalId | — |
| AC1.5 | `phase_02.md` Task 4 CLI: `az deployment sub what-if` gate-off | — |
| AC2.1 | — | B1 |
| AC2.2 | — | B2 |
| AC2.3 | — | B3 |
| AC2.4 | grep over `pipeline-identity.json` rendered ARM | — |
| AC2.5 | grep `if (!empty(...))` count == 4 in `pipeline-identity.bicep` | — |
| AC3.1 | — | A1, A2 |
| AC3.2 | `phase_02.md` Task 4 CLI: validate staging + prod bicepparam | — |
| AC3.3 | — | A5 |
| AC4.1 | — | C1 |
| AC4.2 | — | C2 |
| AC4.3 | — | C3 |
| AC4.4 | — | C4 |
| AC4.5 | grep `### 3\.5\.[1-8] ` returns 8 lines in `bootstrap.md` | — |
| AC5.1 | `test_create_submission_happy_path_dispatches_with_real_secret_name` | — |
| AC5.2 | grep `PLACEHOLDER` in `pipeline_dispatch/` + `submissions/` services | — |
| AC5.3 | grep `dispatch_for_submission` in `create.py` + `retry.py` | — |
| AC5.4 | `test_create_submission_happy_path_dispatches_with_real_secret_name` | — |
| AC6.1 | `test_create_submission_no_orphan_row_on_dispatch_503` + `test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset` | — |
| AC6.2 | `test_create_submission_no_orphan_row_on_dispatch_503` | — |
| AC6.3 | `test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset` + `..._when_pipeline_kv_uri_empty` + `test_create_submission_no_orphan_row_on_dispatch_503` | — |
| AC6.4 | `test_create_submission_idempotency_replays_503_on_retry` | — |
| AC6.5 | `test_no_silent_no_op_log_line_on_dispatch_skip` | — |
| AC7.1 | — | D1 |
| AC7.2 | — | D2 |
| AC7.3 | — | D3 |
| AC7.4 | — | D4 |
| AC7.5 | — | E1–E5 |

## Relevant File Paths

- Test requirements source: `/home/sysop/rac/docs/implementation-plans/2026-04-28-pipeline-trust/test-requirements.md`
- Phase-5 operator runbook (most live verification commands): `/home/sysop/rac/docs/implementation-plans/2026-04-28-pipeline-trust/phase_05.md`
- Bootstrap runbook §3.5 (operator-friendly form): `/home/sysop/rac/docs/runbooks/bootstrap.md`
- Bicep module under test: `/home/sysop/rac/infra/modules/pipeline-identity.bicep`
- Rendered ARM (commit-tracked): `/home/sysop/rac/infra/modules/pipeline-identity.json`
- Backend route tests: `/home/sysop/rac/apps/control-plane/backend/tests/test_submissions_api.py`
- Backend helper tests: `/home/sysop/rac/apps/control-plane/backend/tests/test_dispatch_helper.py`
- Dispatch helper source: `/home/sysop/rac/apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/dispatch_helper.py`
- Submission create source: `/home/sysop/rac/apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py`
- Admin retry source: `/home/sysop/rac/apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py`
