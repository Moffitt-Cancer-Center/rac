# Pipeline Trust — Test Requirements

Maps every acceptance criterion in the design plan
[`docs/design-plans/2026-04-28-pipeline-trust.md`](../../design-plans/2026-04-28-pipeline-trust.md)
to either an automated test (with expected file path and test name) or a
human verification step (with the tool/UI/dashboard the operator inspects).

## Coverage Summary

- Total ACs: 25
- Automated: 13
- Human verification: 12

The split reflects that a large share of this plan's surface is bicep
deployment + live Azure/GitHub state, which has no realistic automated
substitute (mocking the entire ARM control plane + GH Actions OIDC
exchange is more brittle than a five-minute operator check).

---

## Automated Tests

### pipeline-trust.AC1: Per-env identity + FIC provisioned via bicep

#### pipeline-trust.AC1.3
- Type: structural-grep
- File: `infra/modules/pipeline-identity.bicep` (source) + `infra/modules/pipeline-identity.json` (rendered)
- Test name: `grep "api://AzureADTokenExchange" infra/modules/pipeline-identity.bicep` returns exactly one match; `grep -o "api://AzureADTokenExchange" infra/modules/pipeline-identity.json | wc -l` >= 1
- Description: Asserts `audiences` is hardcoded to the exact GitHub-Actions audience string with no extras/typos. Codified directly in the module source; no parameter substitution.
- Produced by: phase_01.md Task 3 (verification block).

#### pipeline-trust.AC1.4
- Type: integration (CLI)
- File: phase_02.md Task 4 verification step "AC1.4 — gate-on with empty principalId fails validate"
- Test name: `az deployment sub validate --parameters deployPipelineIdentity=true pipelineAppPrincipalIdDev=''` exits non-zero
- Description: When the gate is on but the principal ID is empty, the conditional `name:` expression in `main.bicep` resolves to `''` and ARM rejects with `'name' property is required and cannot be empty`. Run as part of phase_02 acceptance gate; reproducible on any branch.
- Produced by: phase_02.md Task 2 (mechanism) + Task 4 (verification command).

#### pipeline-trust.AC1.5
- Type: integration (CLI)
- File: phase_02.md Task 4 verification step "AC1.5 — gate-off what-if vs live dev RG = empty diff"
- Test name: `az deployment sub what-if` against dev RG with default params yields "no change"
- Description: With `deployPipelineIdentity=false` (default) and `deployPipelineKv=false`, both `pipelineKv` and `pipelineIdentity` module invocations are gated; `what-if` against the live dev RG must show zero resource changes. Re-runnable.
- Produced by: phase_02.md Task 2 (gate mechanism) + Task 4 (verification command).

### pipeline-trust.AC2: Per-resource least-privilege RBAC

#### pipeline-trust.AC2.4
- Type: structural-grep
- File: `infra/modules/pipeline-identity.json` (rendered ARM) + `infra/modules/pipeline-identity.bicep` (secondary)
- Test name: `grep -E '"scope":\s*"\[resourceGroup\(\)\.id\]"|"scope":\s*"\[subscription\(\)\.id\]"' infra/modules/pipeline-identity.json` returns zero matches; `grep -E '"scope":\s*"\[resourceId\(.Microsoft\.KeyVault/vaults., .kv-rac-[^p]' infra/modules/pipeline-identity.json` returns zero matches
- Description: AC2.4 explicitly says "grep over the deployed bicep `.json` artifacts". Confirms no role assignment is RG/subscription/platform-KV scoped.
- Produced by: phase_01.md Task 3 (verification block — both source-grep and rendered-template-grep).

#### pipeline-trust.AC2.5
- Type: structural-grep
- File: `infra/modules/pipeline-identity.bicep`
- Test name: `grep -c "if (!empty(pipelineAppPrincipalId)" infra/modules/pipeline-identity.bicep` returns 4
- Description: Each of the four role assignments is wrapped in `if (!empty(pipelineAppPrincipalId) && !empty(<scope-id>))`; an empty principal short-circuits all assignments.
- Produced by: phase_01.md Task 3 (verification block).

### pipeline-trust.AC3: Dev provisioned; staging/prod codified, not deployed

#### pipeline-trust.AC3.2
- Type: integration (CLI)
- File: phase_02.md Task 4 verification step "AC3.2 — staging + prod validate cleanly (gate off)"
- Test name: `for env in staging prod; do az deployment sub validate --parameters infra/environments/${env}.bicepparam ...` both exit 0
- Description: Adding the new pipeline-trust params to staging/prod bicepparam files must not introduce new validation failures. Gate stays off; codified-not-deployed.
- Produced by: phase_02.md Task 3 (param stubs) + Task 4 (verification command).

### pipeline-trust.AC5: `create.py` placeholder-secret bug fixed

#### pipeline-trust.AC5.1
- Type: integration
- File: `apps/control-plane/backend/tests/test_submissions_api.py::test_create_submission_happy_path_dispatches_with_real_secret_name`
- Test name: `test_create_submission_happy_path_dispatches_with_real_secret_name`
- Description: Mocks `mint_callback_secret` and `gh_dispatch.dispatch`; POSTs `/api/submissions` with full happy-path env (gh_pat set, pipeline_kv_uri set); asserts the dispatched payload contains `client_payload['callback_secret_name'] == f'rac-pipeline-cb-{response.json()["id"]}'`.
- Produced by: phase_03.md Task 7 (test) + Task 6 (production-code fix).

#### pipeline-trust.AC5.2
- Type: structural-grep
- File: `apps/control-plane/backend/src/rac_control_plane/services/`
- Test name: `grep -rn "PLACEHOLDER\|placeholder" apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/ apps/control-plane/backend/src/rac_control_plane/services/submissions/` returns zero matches
- Description: After the refactor, no `PLACEHOLDER` string remains in dispatch code paths.
- Produced by: phase_03.md Task 6 (removes placeholder) + Task 8 (verification grep).

#### pipeline-trust.AC5.3
- Type: structural-grep
- File: `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py` and `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py`
- Test name: `grep -l "dispatch_for_submission" create.py retry.py` lists both files
- Description: Both flows (original-create and admin-retry) import and call `dispatch_for_submission`.
- Produced by: phase_03.md Task 5 (retry refactor) + Task 6 (create refactor) + Task 8 (verification grep).

#### pipeline-trust.AC5.4
- Type: integration (regression)
- File: `apps/control-plane/backend/tests/test_submissions_api.py::test_create_submission_happy_path_dispatches_with_real_secret_name`
- Test name: same as AC5.1
- Description: This is the failure-mode framing of AC5.1 — if a future regression bypasses the helper and reintroduces a placeholder, the assertion `callback_secret_name == f"rac-pipeline-cb-{submission_id}"` fails loudly. Test name in plan, no separate file.
- Produced by: phase_03.md Task 7.

### pipeline-trust.AC6: Loud-fail on missing PAT

#### pipeline-trust.AC6.1
- Type: integration
- File: `apps/control-plane/backend/tests/test_submissions_api.py::test_create_submission_no_orphan_row_on_dispatch_503`
- Test name: `test_create_submission_no_orphan_row_on_dispatch_503`
- Description: With `RAC_GH_PAT` unset (`monkeypatch.delenv` + `get_settings.cache_clear()`), POST `/api/submissions` returns 503 with body `{"code": "service_unavailable", "message": "...RAC_GH_PAT...", "correlation_id": "..."}`. Helper-level coverage also at `tests/test_dispatch_helper.py::test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset`.
- Produced by: phase_03.md Task 3 (helper test) + Task 4 (helper impl) + Task 7 (route test + production code).

#### pipeline-trust.AC6.2
- Type: integration
- File: `apps/control-plane/backend/tests/test_submissions_api.py::test_create_submission_no_orphan_row_on_dispatch_503`
- Test name: `test_create_submission_no_orphan_row_on_dispatch_503`
- Description: Same test as AC6.1 also asserts that the `submission` table count is unchanged after the 503 — the `DispatchUnavailableError` is raised before `session.commit()`, so no DB row leaks.
- Produced by: phase_03.md Task 7.

#### pipeline-trust.AC6.3
- Type: integration
- File: `apps/control-plane/backend/tests/test_dispatch_helper.py::test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset` (and `..._when_pipeline_kv_uri_empty`) + `apps/control-plane/backend/tests/test_submissions_api.py::test_create_submission_no_orphan_row_on_dispatch_503`
- Test name: `test_dispatch_for_submission_raises_DispatchUnavailableError_when_gh_pat_unset`, `test_dispatch_for_submission_raises_DispatchUnavailableError_when_pipeline_kv_uri_empty`
- Description: Mocks `mint_callback_secret`; asserts it was NOT called when the helper bails on missing config (helper checks happen BEFORE any I/O). Route-level test also asserts no KV write.
- Produced by: phase_03.md Task 3 (failing tests) + Task 4 (helper impl with order-of-checks).

#### pipeline-trust.AC6.4
- Type: integration
- File: `apps/control-plane/backend/tests/test_submissions_api.py::test_create_submission_idempotency_replays_503_on_retry`
- Test name: `test_create_submission_idempotency_replays_503_on_retry`
- Description: With `RAC_GH_PAT` unset and a fixed `Idempotency-Key`, POST twice — both responses are 503 with the same correlation_id. The second request does not invoke `create_submission` (verified via spy/monkeypatch).
- Produced by: phase_03.md Task 7.

#### pipeline-trust.AC6.5
- Type: structural-grep
- File: `apps/control-plane/backend/src/`
- Test name: `grep -rn "pipeline_dispatch_skipped_no_auth_token\|_build_dispatch_fn" apps/control-plane/backend/src/` returns zero matches; alternatively a one-liner pytest check `test_no_silent_no_op_log_line_on_dispatch_skip` in `tests/test_submissions_api.py` that opens `submissions.py` via `pathlib` and asserts the literal string is absent.
- Description: The silent-no-op factory and its log line are structurally removed.
- Produced by: phase_03.md Task 7 (deletes `_build_dispatch_fn`) + Task 8 (grep).

---

## Human Verification

The following ACs verify the *runtime state* of a live Azure tenant and a
live GitHub repo. Automating these would require either (a) a full Azure +
Microsoft Graph + GitHub Actions integration harness, or (b) heavy mocking
that does not actually prove the trust topology works against real OIDC
issuers. The cost-benefit clearly favors operator inspection of explicit
CLI output, with the session log captured to `/tmp/phase5-deploy.log` and
attached to the implementation-plan PR.

### pipeline-trust.AC1.1
- Why automation infeasible: requires the FIC to actually exist in Moffitt's Entra tenant after a Pass-2 deploy; no realistic CI substitute that exercises Microsoft Graph FIC creation against a real app reg.
- Verification approach: operator runs `az ad app federated-credential list --id "$PIPELINE_APP_OBJECT_ID" --query "[?name=='rac-pipeline-dev-gha'].subject" -o tsv`.
- Pass criteria: stdout is exactly `repo:jdkruzr/rac-pipeline:environment:dev`.
- Produced by phase_05.md Task 4 (deploy) + Task 8 (final-sweep query).

### pipeline-trust.AC1.2
- Why automation infeasible: the proof is structural ("multi-env-ready"), but the rendered ARM template's parameter expression resolves at deploy time, not at compile time. A rendered template grep against `infra/modules/pipeline-identity.json` could partially substitute, but the design plan AC explicitly cites `az deployment sub validate` + `what-if` against the staging param file with stub IDs as the verification path.
- Verification approach: operator runs phase_05.md Task 5's two-pronged check — `what-if` against `staging.bicepparam` with stub principal IDs (best-effort) plus a structural inspection of `main.bicep`'s rendered FIC template confirming the subject is parameter-driven on `racEnv`.
- Pass criteria: rendered FIC subject template is `repo:[parameters('controlPlaneGithubPipelineOwner')]/[parameters('controlPlaneGithubPipelineRepo')]:environment:[parameters('racEnv')]`. Plugging `racEnv='staging'` produces `environment:staging`.
- Produced by phase_05.md Task 5.

### pipeline-trust.AC2.1
- Why automation infeasible: requires inspecting actual Azure RBAC role-assignment records on the live ACR after deploy. Mocking ARM authorization records is not equivalent to verifying the deployed state.
- Verification approach: `az role assignment list --assignee "$SP_OBJECT_ID" --query "[].{role:roleDefinitionName,scope:scope}" -o json | jq` and confirm the AcrPull + AcrPush rows have `scope` ending in `/Microsoft.ContainerRegistry/registries/<acr-name>` (not RG, not subscription).
- Pass criteria: exactly two ACR-related entries; both scopes end at the registry resource.
- Produced by phase_05.md Task 4 + Task 8.

### pipeline-trust.AC2.2
- Why automation infeasible: same as AC2.1 — requires inspecting the live KV scope. Critically, this AC enforces the platform-KV vs pipeline-KV blast-radius separation; verifying it requires reading the actual scope string from the deployed assignment.
- Verification approach: same `az role assignment list` query as AC2.1; for the Key Vault Secrets User entry, confirm the scope ends in `/Microsoft.KeyVault/vaults/kv-rac-pl-...` (NOT `kv-rac-...` without the `pl-` infix).
- Pass criteria: exactly one KV-scoped entry, scope contains `kv-rac-pl-`.
- Produced by phase_05.md Task 4 + Task 8.

### pipeline-trust.AC2.3
- Why automation infeasible: same as AC2.1 — requires reading the deployed container-level scope.
- Verification approach: same `az role assignment list` query; for the Storage Blob Data Contributor entry, confirm the scope ends in `/Microsoft.Storage/.../containers/scan-artifacts` (not the storage account `/storageAccounts/<x>`).
- Pass criteria: exactly one Storage entry, scope path includes `/containers/scan-artifacts`.
- Produced by phase_05.md Task 4 + Task 8.

### pipeline-trust.AC3.1
- Why automation infeasible: requires querying Microsoft Graph for the post-deploy app-reg state in the live tenant.
- Verification approach: `az ad app list --display-name 'rac-pipeline-dev' --query "length(@)" -o tsv` and `az role assignment list --assignee "$SP_OBJECT_ID" --query "length(@)" -o tsv`.
- Pass criteria: app count == 1; role-assignment count == 4.
- Produced by phase_05.md Task 4 + Task 8.

### pipeline-trust.AC3.3
- Why automation infeasible: requires `az deployment sub create` (not just validate) against a staging-shaped param file with the gate on but principal empty. The AC text says "fails before any resource is created" — this is an Azure ARM behavior we observe operationally.
- Verification approach: phase_05.md Task 5's command `az deployment sub validate ... --parameters deployPipelineIdentity=true pipelineAppPrincipalIdDev=''` (or sub create — operator's choice) and confirm non-zero exit + error message.
- Pass criteria: non-zero exit; stderr/stdout mentions "name property required" or equivalent ARM template-validation error.
- Produced by phase_05.md Task 5.

### pipeline-trust.AC4.1
- Why automation infeasible: requires the GH Environment to exist on `jdkruzr/rac-pipeline`, which is created manually per runbook §3.5.3.
- Verification approach: `gh api /repos/jdkruzr/rac-pipeline/environments/dev --jq '.name'`.
- Pass criteria: stdout is `"dev"`; HTTP status 200.
- Produced by phase_05.md Task 2 (configures GH Env) + Task 8 (verification query).

### pipeline-trust.AC4.2
- Why automation infeasible: same as AC4.1 — requires inspecting live GH repo state.
- Verification approach: `gh secret list --env dev --repo jdkruzr/rac-pipeline | awk '{print $1}'`.
- Pass criteria: output includes all of `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
- Produced by phase_05.md Task 2 + Task 8.

### pipeline-trust.AC4.3
- Why automation infeasible: same as AC4.1.
- Verification approach: `gh variable list --env dev --repo jdkruzr/rac-pipeline | awk '{print $1}'`.
- Pass criteria: output includes all of `ACR_NAME`, `ACR_LOGIN_SERVER`, `BLOB_ACCOUNT_URL`, `KV_NAME`, `SEVERITY_GATE`.
- Produced by phase_05.md Task 2 + Task 8.

### pipeline-trust.AC4.4
- Why automation infeasible: same as AC4.1; the value is a string-equality check against the deployed pipeline KV name.
- Verification approach: `gh variable get KV_NAME --env dev --repo jdkruzr/rac-pipeline` and confirm prefix.
- Pass criteria: value starts with `kv-rac-pl-`, NOT just `kv-rac-`.
- Produced by phase_05.md Task 4 (backfill after deploy reveals real KV name) + Task 8.

### pipeline-trust.AC4.5
- Why automation infeasible: structurally a docs-content check; could theoretically be automated as a markdown grep, but the design plan AC defines passing as "the section is written and contains the eight ordered substeps" — best confirmed by review.
- Verification approach: review `docs/runbooks/bootstrap.md` for the `## 3.5 Pipeline Trust Setup` heading and the eight substeps `### 3.5.1` through `### 3.5.8`. Sanity grep `grep -nE "### 3\.5\.[1-8] " docs/runbooks/bootstrap.md` returns 8 lines.
- Pass criteria: section exists, substeps 3.5.1–3.5.8 present, design-plan cross-link resolves.
- Produced by phase_04.md Task 1 (section authored) + Task 3 (cross-reference verification).

### pipeline-trust.AC7.1
- Why automation infeasible: requires GH Actions OIDC token exchange against Azure Entra ID. Cannot mock without losing the entire test value.
- Verification approach: in the GitHub Actions UI for `jdkruzr/rac-pipeline`, click into the workflow run triggered by the admin retry; click the `Azure login (OIDC)` step and confirm exit 0 plus successful token exchange in the log tail.
- Pass criteria: step status green; log shows `Login successful` or equivalent.
- Produced by phase_05.md Task 6.

### pipeline-trust.AC7.2
- Why automation infeasible: requires the live workflow to read from the live pipeline KV using the OIDC-issued token. Same rationale as AC7.1.
- Verification approach: in the GitHub Actions UI, the `Fetch callback secret` step's log shows `rac-pipeline-cb-${SUBMISSION_ID}` and a successful `az keyvault secret show` against `kv-rac-pl-...-dev`.
- Pass criteria: step exits 0; log line cites the right secret name and the pipeline KV (not platform KV).
- Produced by phase_05.md Task 6.

### pipeline-trust.AC7.3
- Why automation infeasible: requires end-to-end through HMAC-signed callback to the deployed control plane.
- Verification approach: tail the control-plane container logs — `az containerapp logs show --name 'ca-rac-controlplane-dev' --resource-group rg-rac-dev --tail 100 | grep -E "callback_received|callback_signature_verified|callback_accepted"`.
- Pass criteria: at least one match for the test submission_id, and the workflow run status in GH UI is success.
- Produced by phase_05.md Task 6.

### pipeline-trust.AC7.4
- Why automation infeasible: depends on the full pipeline run completing and a real callback being processed.
- Verification approach: `curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "${CONTROL_PLANE_HOST}/api/submissions/${SUBMISSION_ID}" | jq '.status'`.
- Pass criteria: status is `awaiting_research_review` or `severity_gate_failed`; explicitly NOT `awaiting_scan`.
- Produced by phase_05.md Task 6.

### pipeline-trust.AC7.5
- Why automation infeasible: AC text explicitly says "in-prod replication of AC6.1" — requires actually redeploying the live control plane with `RAC_GH_PAT` removed from its env block. AC6.1 is the automated equivalent at unit/integration level; AC7.5 is the production-replica.
- Verification approach: phase_05.md Task 7's `az containerapp update --remove-env-vars RAC_GH_PAT` followed by `curl -i POST /api/submissions`. Cleanup mandatory (restore the env var) per Task 7's "Cleanup" block.
- Pass criteria: response status `503`; body contains `"code":"service_unavailable"` and `RAC_GH_PAT`; DB query for new submissions in the test window returns 0.
- Produced by phase_05.md Task 7.

---

## Cross-reference summary

| AC | Phase produced | Phase verified |
|----|----------------|----------------|
| pipeline-trust.AC1.1 | phase_01 (module) + phase_02 (gate) | phase_05 Task 4+8 (human) |
| pipeline-trust.AC1.2 | phase_01 (module) | phase_05 Task 5 (human) |
| pipeline-trust.AC1.3 | phase_01 Task 3 | phase_01 Task 3 grep (auto) |
| pipeline-trust.AC1.4 | phase_02 Task 2 | phase_02 Task 4 (auto, CLI) |
| pipeline-trust.AC1.5 | phase_02 Task 2 | phase_02 Task 4 (auto, CLI) |
| pipeline-trust.AC2.1 | phase_01 Task 3 | phase_05 Task 4+8 (human) |
| pipeline-trust.AC2.2 | phase_01 Task 3 | phase_05 Task 4+8 (human) |
| pipeline-trust.AC2.3 | phase_01 Task 3 | phase_05 Task 4+8 (human) |
| pipeline-trust.AC2.4 | phase_01 Task 3 | phase_01 Task 3 grep (auto) |
| pipeline-trust.AC2.5 | phase_01 Task 3 | phase_01 Task 3 grep (auto) |
| pipeline-trust.AC3.1 | phase_05 Task 1+4 | phase_05 Task 8 (human) |
| pipeline-trust.AC3.2 | phase_02 Task 3 | phase_02 Task 4 (auto, CLI) |
| pipeline-trust.AC3.3 | phase_02 Task 2 | phase_05 Task 5 (human) |
| pipeline-trust.AC4.1 | phase_05 Task 2 | phase_05 Task 8 (human) |
| pipeline-trust.AC4.2 | phase_05 Task 2 | phase_05 Task 8 (human) |
| pipeline-trust.AC4.3 | phase_05 Task 2 | phase_05 Task 8 (human) |
| pipeline-trust.AC4.4 | phase_05 Task 4 | phase_05 Task 8 (human) |
| pipeline-trust.AC4.5 | phase_04 Task 1 | phase_04 Task 3 (human review) |
| pipeline-trust.AC5.1 | phase_03 Task 4+6 | phase_03 Task 7 test (auto) |
| pipeline-trust.AC5.2 | phase_03 Task 6 | phase_03 Task 8 grep (auto) |
| pipeline-trust.AC5.3 | phase_03 Task 5+6 | phase_03 Task 8 grep (auto) |
| pipeline-trust.AC5.4 | phase_03 Task 4+6 | phase_03 Task 7 test (auto) |
| pipeline-trust.AC6.1 | phase_03 Task 4+7 | phase_03 Task 7 test (auto) |
| pipeline-trust.AC6.2 | phase_03 Task 7 | phase_03 Task 7 test (auto) |
| pipeline-trust.AC6.3 | phase_03 Task 4 | phase_03 Task 3+7 tests (auto) |
| pipeline-trust.AC6.4 | phase_03 Task 7 | phase_03 Task 7 test (auto) |
| pipeline-trust.AC6.5 | phase_03 Task 7 | phase_03 Task 8 grep (auto) |
| pipeline-trust.AC7.1 | phase_05 Task 6 | phase_05 Task 6 (human) |
| pipeline-trust.AC7.2 | phase_05 Task 6 | phase_05 Task 6 (human) |
| pipeline-trust.AC7.3 | phase_05 Task 6 | phase_05 Task 6 (human) |
| pipeline-trust.AC7.4 | phase_05 Task 6 | phase_05 Task 6 (human) |
| pipeline-trust.AC7.5 | phase_05 Task 7 | phase_05 Task 7 (human) |

(Total = 25 ACs by individual sub-criterion, despite the table aggregation by parent.)
