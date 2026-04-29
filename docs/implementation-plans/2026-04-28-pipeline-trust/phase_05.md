# Pipeline Trust Implementation Plan — Phase 5: Dev Deploy & End-to-End Verification

**Goal:** Execute `bootstrap.md` §3.5 (added in Phase 4) against the live `rg-rac-dev` resource group in Moffitt's subscription. Provision the `rac-pipeline-dev` Entra app reg, configure the GH Environment at `jdkruzr/rac-pipeline`, run the Pass-2 bicep deploy, then run two end-to-end tests: (1) admin retry on a previously-stuck submission must complete the build-and-scan pipeline through to FSM advance; (2) a control plane with `RAC_GH_PAT` deliberately unset must return 503 on a fresh submission with no DB side-effect.

**Architecture:** Operational. No new code, no new bicep modules. The implementor here is the operator running CLI commands against a live Azure subscription + a live GitHub repo. All commands and verification queries are pre-written in `bootstrap.md` §3.5 (Phase 4 deliverable); this phase just runs them and checks the output.

**Tech Stack:** `az` CLI, `gh` CLI, the live RAC dev deployment in eastus2.

**Scope:** Phase 5 of 5. Final phase. After this, the pipeline-trust feature is shippable.

**Codebase verified:** 2026-04-28. Confirmed: admin retry route is `POST /api/admin/submissions/{submission_id}/dispatch/retry` (router prefix `/admin` mounted at `/api`, route definition in `provisioning.py:264`). The control-plane container app supports redeploy via the existing bicep `deployControlPlaneApp=true` gate; toggling `RAC_GH_PAT` is an env-var change at the bicep layer (paramfile or runtime override).

**External research findings:** None new beyond what Phase 4 captured.

---

## Acceptance Criteria Coverage

This phase **operationally verifies** every AC that earlier phases laid the foundation for. It is the only phase where the live tenant is touched.

### pipeline-trust.AC1: Per-env identity + FIC provisioned via bicep
- **pipeline-trust.AC1.1 Success:** bicep module deployed with `racEnv='dev'` produces a federated identity credential whose `subject` is exactly `repo:jdkruzr/rac-pipeline:environment:dev`.
- **pipeline-trust.AC1.2 Success:** the same module invoked with `racEnv='staging'` (validated via `az deployment sub validate` + `what-if`, not actually deployed in this plan) produces FIC subject `repo:jdkruzr/rac-pipeline:environment:staging` — proves multi-env-ready.

### pipeline-trust.AC2: Per-resource least-privilege RBAC
- **pipeline-trust.AC2.1 Success:** AcrPull + AcrPush role assignments are scoped to the env ACR's `resourceId`, not RG, not subscription.
- **pipeline-trust.AC2.2 Success:** KV Secrets User role assignment is scoped to the env's **pipeline KV** (`kv-rac-pl-...`), NOT the platform KV (`kv-rac-...`).
- **pipeline-trust.AC2.3 Success:** Storage Blob Data Contributor is scoped to the artifacts container's `resourceId`, not the storage account.

### pipeline-trust.AC3: Dev provisioned; staging/prod codified, not deployed
- **pipeline-trust.AC3.1 Success:** post-Phase-5, `az ad app list --display-name 'rac-pipeline-dev'` returns one app reg; `az role assignment list --assignee <principalId>` shows exactly the four expected assignments.
- **pipeline-trust.AC3.3 Failure:** attempting `az deployment sub create` for staging with `deployPipelineIdentity=true` and empty `pipelineAppObjectIdStaging` fails before any resource is created. *(Operationally validated by attempting the deploy and confirming it errors.)*

### pipeline-trust.AC4: GitHub repo dev Environment configured per runbook
- **pipeline-trust.AC4.1 Success:** `gh api /repos/jdkruzr/rac-pipeline/environments/dev` returns 200.
- **pipeline-trust.AC4.2 Success:** `gh secret list --env dev --repo jdkruzr/rac-pipeline` shows all three of `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
- **pipeline-trust.AC4.3 Success:** `gh variable list --env dev --repo jdkruzr/rac-pipeline` shows all five of `ACR_NAME`, `ACR_LOGIN_SERVER`, `BLOB_ACCOUNT_URL`, `KV_NAME`, `SEVERITY_GATE`.
- **pipeline-trust.AC4.4 Success:** the value of `KV_NAME` equals the pipeline KV's name (`kv-rac-pl-...`), NOT the platform KV's name.

### pipeline-trust.AC7: End-to-end verification on live dev
- **pipeline-trust.AC7.1 Success:** a previously-stuck submission re-dispatched via `POST /api/admin/submissions/{id}/dispatch/retry` produces a GH Actions workflow run where the `Azure login (OIDC)` step exits 0 (was failing before this plan).
- **pipeline-trust.AC7.2 Success:** the same workflow's `Fetch callback secret` step successfully reads `rac-pipeline-cb-{submission_id}` from `kv-rac-pl-dev`.
- **pipeline-trust.AC7.3 Success:** the workflow completes build+scan, POSTs an HMAC-signed callback to the control plane, and the callback is accepted (HTTP 200).
- **pipeline-trust.AC7.4 Success:** the submission FSM advances out of `awaiting_scan` to either `awaiting_research_review` or `severity_gate_failed` (depending on the test image's scan verdict).
- **pipeline-trust.AC7.5 Failure:** a fresh submission attempted while the live control plane has `RAC_GH_PAT` unset returns 503 (in-prod replication of AC6.1).

**Verifies in tests:** None automated — this is the human-verification phase. Tasks 1-7 each list explicit `az` / `gh` / `curl` invocations whose output the operator manually inspects against a stated expectation. Test-analyst will fold these into the human test plan after Finalization.

---

## Notes for the Implementor / Operator

- **You are running this against a live Azure subscription and a live GitHub repo.** Read every command before running it. Some are destructive (deleting / overwriting GH secrets is fine — they're easy to recreate; rolling back an app-reg ownership change is a few clicks).
- **All commands live in `docs/runbooks/bootstrap.md` §3.5** (Phase 4 deliverable). This phase's tasks reference the runbook substeps by number — do not duplicate the commands here.
- **Capture a paste-able log.** Open a fresh terminal session, `script -a /tmp/phase5-deploy.log` at start, run everything inside it. The session log becomes the artifact attached to the implementation-plan PR.
- **Mark each task complete only when its verification command output matches the stated expectation.** No "looks fine" — the AC table cites exact CLI return values.
- **If anything fails during 5.5 (admin retry e2e), stop and triage.** Do NOT proceed to 5.6 (the loud-fail test) on a half-broken trust setup. Common failures:
  - `AADSTS70021` / `AADSTS700213` on `Azure login (OIDC)` → FIC subject mismatch. Re-run §3.5.7 verification.
  - 403 on `Fetch callback secret` → RBAC not propagated yet (wait 5 more min) OR `KV_NAME` wrong.
  - Workflow doesn't trigger at all → repo PAT `RAC_GH_PAT` invalid (rotate per `docs/runbooks/bootstrap.md` §6).
- **The "loud-fail" test (Task 6) involves redeploying the control plane with `RAC_GH_PAT` unset.** This breaks the dev deploy until you redeploy with the PAT restored (Task 7). Schedule a maintenance window of ~10 min for this if other people use the dev tenant.

---

<!-- START_TASK_1 -->
### Task 1: Provision the `rac-pipeline-dev` Entra app registration

**Verifies:** sets up state required by all subsequent tasks; no AC directly satisfied yet.

**Files:** none (operational).

**Implementation:**

Execute `docs/runbooks/bootstrap.md` §3.5.1 + §3.5.2 against the live Moffitt tenant + the operator's local `gh` and `az` CLIs.

Capture the four IDs (`PIPELINE_APP_ID`, `PIPELINE_APP_OBJECT_ID`, `PIPELINE_APP_UNIQUENAME`, `SP_OBJECT_ID`) into a scratchpad you can paste from in Tasks 2-3.

**Verification:**

```bash
# §3.5.1 success
az ad app list --display-name 'rac-pipeline-dev' --query "[].{id:id,appId:appId,displayName:displayName}" -o table
# Expected: exactly one row.

# §3.5.2 success — deploy SP is now Owner
az ad app owner list --id "$PIPELINE_APP_OBJECT_ID" --query "[].id" -o tsv
# Expected: includes the rac-infra-deploy SP's object ID.
```

**Commit:** None (no source file changes; the `bicepparam` updates land in Task 3).

<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Configure GitHub Environment + secrets + variables for `dev`

**Verifies:** pipeline-trust.AC4.1, AC4.2, AC4.3, AC4.4.

**Files:** none (operational).

**Implementation:**

Execute `docs/runbooks/bootstrap.md` §3.5.3 against `jdkruzr/rac-pipeline`.

> **Heads-up on `KV_NAME`:** at this point the pipeline KV does NOT yet exist (it's created by the Pass-2 deploy in Task 4). Therefore you do not yet know the exact `kv-rac-pl-...-dev` name. Two options:
> 1. **Skip the `KV_NAME` step for now**; come back after Task 4 with the deployed name from `az deployment sub show ... --query properties.outputs.pipelineKvName.value -o tsv`.
> 2. **Pre-compute the name** by running `az deployment sub what-if` with the gate flipped to `true` and stub IDs and reading the proposed resource name from the diff. (Option 1 is simpler and lower-risk.)
>
> Recommend Option 1. Run §3.5.3 setting `KV_NAME=''` (empty) initially and re-run just the single `gh variable set KV_NAME ...` command after Task 4 with the real name.

**Verification:**

```bash
# AC4.1
gh api "/repos/jdkruzr/rac-pipeline/environments/dev" --jq '.name'
# Expected: "dev". HTTP 200.

# AC4.2
gh secret list --env dev --repo jdkruzr/rac-pipeline | awk '{print $1}'
# Expected: includes AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID.

# AC4.3
gh variable list --env dev --repo jdkruzr/rac-pipeline | awk '{print $1}'
# Expected: includes ACR_NAME, ACR_LOGIN_SERVER, BLOB_ACCOUNT_URL, KV_NAME, SEVERITY_GATE.
# (KV_NAME may be empty until Task 4 completes — re-run after Task 4.)
```

**AC4.4 verification** (after Task 4 sets the real name):

```bash
gh variable get KV_NAME --env dev --repo jdkruzr/rac-pipeline
# Expected: starts with 'kv-rac-pl-' (NOT just 'kv-rac-').
```

**Commit:** None (operational).

<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Update `infra/environments/dev.bicepparam` with captured IDs

**Verifies:** preparation for Task 4.

**Files:**
- Modify: `infra/environments/dev.bicepparam`

**Implementation:**

Execute `bootstrap.md` §3.5.4. Edit `infra/environments/dev.bicepparam`:
- Flip `deployPipelineKv = false` → `true`.
- Flip `deployPipelineIdentity = false` → `true`.
- Set `pipelineAppUniqueNameDev = '<PIPELINE_APP_UNIQUENAME>'`.
- Set `pipelineAppPrincipalIdDev = '<SP_OBJECT_ID>'`.
- Set `pipelineAppClientIdDev = '<PIPELINE_APP_ID>'`.

(Values from Task 1's scratchpad.)

**Verification:**

```bash
cd /home/sysop/rac

grep -A1 "deployPipelineKv\|deployPipelineIdentity\|pipelineApp" infra/environments/dev.bicepparam
# Expected: deployPipelineKv = true, deployPipelineIdentity = true,
# three pipelineApp* params set to non-empty values.

# Validate with the new values
az deployment sub validate \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID"
# Expected: exit 0.
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/environments/dev.bicepparam
git commit -m "infra(env/dev): wire pipeline-trust IDs (Phase 5 Pass-2)

Flips deployPipelineKv + deployPipelineIdentity gates on and threads
the captured rac-pipeline-dev app reg / SP / client IDs.

Captured from manual app-reg provisioning per docs/runbooks/bootstrap.md
§3.5.1. Staging/prod remain gate-off — those environments are
codified in bicep but not deployed in this plan's scope (DoD #3)."
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Pass-2 deploy (gates flipped on, live `rg-rac-dev`)

**Verifies:** pipeline-trust.AC1.1, AC2.1, AC2.2, AC2.3, AC3.1.

**Files:** none (operational; the bicep deploy is the action).

**Implementation:**

Execute `bootstrap.md` §3.5.5. Run the actual `az deployment sub create` against `rg-rac-dev` in eastus2 — capture the deployment name into a variable so subsequent `az deployment sub show` queries can reference it precisely:

```bash
DEPLOY_NAME="rac-dev-pipeline-trust-$(date -u +%Y%m%d-%H%M%S)"

az deployment sub create \
  --name "$DEPLOY_NAME" \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID"

echo "DEPLOY_NAME=$DEPLOY_NAME"  # save for the verification step below
```

Then wait for RBAC propagation per §3.5.6 (5 minutes — do NOT skip).

**Verification:**

```bash
# AC1.1 — FIC subject exact match
az ad app federated-credential list \
  --id "$PIPELINE_APP_OBJECT_ID" \
  --query "[].{name:name,subject:subject,audiences:audiences}" -o json \
  | jq '.[] | select(.name == "rac-pipeline-dev-gha")'
# Expected: subject="repo:jdkruzr/rac-pipeline:environment:dev",
# audiences=["api://AzureADTokenExchange"].

# AC2.1, AC2.2, AC2.3, AC3.1 — exactly four role assignments at per-resource scope
az role assignment list \
  --assignee "$SP_OBJECT_ID" \
  --query "[].{role:roleDefinitionName,scope:scope}" -o json
# Expected: four entries.
# Each scope must contain '/providers/...' and one of:
#   - .../Microsoft.ContainerRegistry/registries/<acr-name>      (AcrPull, AcrPush — AC2.1)
#   - .../Microsoft.KeyVault/vaults/kv-rac-pl-...           (Key Vault Secrets User — AC2.2)
#   - .../Microsoft.Storage/.../containers/scan-artifacts         (Storage Blob Data Contributor — AC2.3)
# NONE of: '/resourceGroups/<rg>$' (RG-only), '/subscriptions/<sub>$' (sub-only),
#          'kv-rac-' WITHOUT 'pipeline' (platform KV).

# AC3.1 — app reg exists
az ad app list --display-name 'rac-pipeline-dev' --query "length(@)" -o tsv
# Expected: 1.

# Capture the deployed pipeline KV name for AC4.4 / Task 2 backfill
az deployment sub show \
  --name "$DEPLOY_NAME" \
  --query 'properties.outputs.pipelineKvName.value' -o tsv
# Expected: 'kv-rac-pl-...-dev'. Pipe this back into the GH variable
# `KV_NAME` (re-run §3.5.3's `gh variable set KV_NAME ...` line).
```

**AC4.4 backfill:**

```bash
PIPELINE_KV_NAME='<from above>'
gh variable set KV_NAME --env dev --repo jdkruzr/rac-pipeline --body "$PIPELINE_KV_NAME"
gh variable get KV_NAME --env dev --repo jdkruzr/rac-pipeline
# Expected: starts with 'kv-rac-pl-'.
```

**Commit:** None (deploys leave no source-file change).

<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: AC1.2 + AC3.3 — staging multi-env validation (no actual deploy)

**Verifies:** pipeline-trust.AC1.2 (multi-env-ready proof), AC3.3 (gate-on with empty principal fails for staging).

**Files:** none (operational; validation only).

**Implementation:**

Run `az deployment sub validate` and `az deployment sub what-if` against the staging param file with the gate flipped on but principal IDs left empty. Expect a failure.

```bash
cd /home/sysop/rac

# AC3.3 — gate on with empty principal must fail validate before resource creation
az deployment sub validate \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/staging.bicepparam \
  --parameters pgAdminPassword='stub' appGwTlsCertKvSecretId='/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/x/providers/Microsoft.KeyVault/vaults/x/secrets/x' \
  --parameters deployPipelineIdentity=true pipelineAppPrincipalIdDev='' \
  ; echo "exit=$?"
# Expected: non-zero exit. Error message includes 'name property required' or similar.

# AC1.2 — gate on with stub principal must produce a FIC with staging subject.
# Two-pronged verification:
#   (a) what-if shows the proposed FIC subject in its diff (best-effort —
#       what-if's text rendering can sometimes elide string properties);
#   (b) rendered .json template — authoritative — explicitly contains the
#       staging subject string. (b) is the load-bearing check.

# (a) what-if (best-effort)
az deployment sub what-if \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/staging.bicepparam \
  --parameters pgAdminPassword='stub' appGwTlsCertKvSecretId='/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/x/providers/Microsoft.KeyVault/vaults/x/secrets/x' \
  --parameters deployPipelineKv=true deployPipelineIdentity=true pipelineAppUniqueNameDev='stub-staging-uniquename' pipelineAppPrincipalIdDev='00000000-0000-0000-0000-000000000001' pipelineAppClientIdDev='00000000-0000-0000-0000-000000000002' \
  > /tmp/whatif-staging.txt 2>&1
grep -E 'subject.*staging|repo:.*environment:staging' /tmp/whatif-staging.txt
# Expected: at least one match (best-effort; may be empty).

# (b) Authoritative — render the bicep + bicepparam to JSON and grep for the
# staging FIC subject. This bypasses what-if's text-rendering vagaries.
az bicep build-params \
  --file infra/environments/staging.bicepparam \
  --stdout \
  > /tmp/staging-params.json
# bicep build-params resolves the bicepparam expression chain at compile time.
# We expect deployPipelineIdentity=false in staging (codified, not deployed),
# so the FIC subject won't appear in the rendered template; instead, force the
# render with explicit overrides via a one-off main.bicep build using stub
# values:
az bicep build --file infra/main.bicep --stdout \
  | python3 -c "import sys, json; t = json.loads(sys.stdin.read()); print(json.dumps(t, indent=2))" \
  | grep -A2 'federatedIdentityCredentials' | head -50
# Expected: shows the FIC resource template with subject literal of the form
# "repo:[parameters('controlPlaneGithubPipelineOwner')]/[parameters('controlPlaneGithubPipelineRepo')]:environment:[parameters('racEnv')]"
# — i.e., the subject is parameter-driven on racEnv. Plugging racEnv='staging'
# at deploy time produces 'environment:staging' as the subject. The structural
# proof is sufficient for AC1.2 ("multi-env-ready").
```

**Verification:** outputs match the expectations above. No actual staging deploy happens.

**Commit:** None.

<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: End-to-end happy-path test on live dev

**Verifies:** pipeline-trust.AC7.1, AC7.2, AC7.3, AC7.4.

**Files:** none (operational; live system test).

**Implementation:**

Execute `bootstrap.md` §3.5.8. Pick a submission stuck in `awaiting_scan` (or create a fresh one — the original-create flow now mints a real secret too, so a brand-new submission will exercise the same paths).

```bash
SUBMISSION_ID='<uuid of stuck or new submission>'
ADMIN_TOKEN='<admin OIDC bearer; see §6 of bootstrap.md>'
CONTROL_PLANE_HOST='https://rac-dev.rac.checkwithscience.com'

# Trigger admin retry (or create a fresh submission via /api/submissions)
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  "${CONTROL_PLANE_HOST}/api/admin/submissions/${SUBMISSION_ID}/dispatch/retry"
# Expected: 200 with body {submission_id, callback_url, dispatched_at}.
```

Then watch `https://github.com/jdkruzr/rac-pipeline/actions` for the new workflow run.

**Verification (manual, in the GH Actions UI):**

1. Workflow run was triggered (AC7.1 prerequisite — a 0-trigger means PAT or repo-dispatch is broken).
2. **AC7.1:** `Azure login (OIDC)` step exits 0. Click into the step log; the last line should show successful token exchange. Compare against pre-plan history (this step was failing before).
3. **AC7.2:** `Fetch callback secret` step exits 0. The log line should show the secret name `rac-pipeline-cb-{SUBMISSION_ID}` and a successful `az keyvault secret show` against `kv-rac-pl-...-dev`.
4. **AC7.3:** workflow completes through to the callback step. The control plane should accept the HMAC callback (HTTP 200). Verify via:

   ```bash
   az containerapp logs show \
     --name 'ca-rac-controlplane-dev' \
     --resource-group rg-rac-dev \
     --tail 100 \
     | grep -E "callback_received|callback_signature_verified|callback_accepted"
   # Expected: at least one match per submission_id.
   ```

5. **AC7.4:** submission FSM advances. Query the API:

   ```bash
   curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
     "${CONTROL_PLANE_HOST}/api/submissions/${SUBMISSION_ID}" \
     | jq '.status'
   # Expected: 'awaiting_research_review' or 'severity_gate_failed'
   # (NOT 'awaiting_scan' — that would mean the callback wasn't accepted).
   ```

**Commit:** None.

<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Loud-fail e2e test — control plane without `RAC_GH_PAT`

**Verifies:** pipeline-trust.AC7.5.

**Files:** none (operational; involves a temporary control-plane redeploy).

**Implementation:**

Redeploy the control plane container app with `RAC_GH_PAT` removed from its env. The cleanest way is to override at the bicep `controlPlaneAcaApp` module's env block, but that's invasive. Faster path: remove the env var via `az containerapp` directly.

```bash
# Remove the RAC_GH_PAT env var from the running CP container app
az containerapp update \
  --name 'ca-rac-controlplane-dev' \
  --resource-group rg-rac-dev \
  --remove-env-vars RAC_GH_PAT

# Wait for the new revision to be active (~30 sec)
sleep 60

# Confirm the env var is gone
az containerapp show \
  --name 'ca-rac-controlplane-dev' \
  --resource-group rg-rac-dev \
  --query "properties.template.containers[0].env[?name=='RAC_GH_PAT']" -o tsv
# Expected: empty.

# Submit a fresh submission and capture the response
RESEARCHER_TOKEN='<researcher OIDC bearer>'
RESPONSE=$(curl -s -i -X POST \
  -H "Authorization: Bearer $RESEARCHER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{...minimal valid submission body...}' \
  "${CONTROL_PLANE_HOST}/api/submissions")

echo "$RESPONSE" | head -30
# Expected: HTTP/1.1 503 ; body includes "code":"service_unavailable" and
# "message":"...RAC_GH_PAT...".

# Verify NO new submission row was inserted (AC6.2 spot-check, also relevant here)
az containerapp exec \
  --name 'ca-rac-controlplane-dev' \
  --resource-group rg-rac-dev \
  --command "psql -c \"SELECT COUNT(*) FROM submission WHERE created_at > NOW() - INTERVAL '5 minutes';\""
# Expected: 0 rows. (Or whatever number existed at the start of this task — i.e., no increase.)
```

**Cleanup (mandatory — restores the dev tenant for further work):**

```bash
# Restore the RAC_GH_PAT env var
az containerapp update \
  --name 'ca-rac-controlplane-dev' \
  --resource-group rg-rac-dev \
  --set-env-vars RAC_GH_PAT=secretref:gh-pat
# (Or whatever env-var-from-secret reference the existing bicep uses;
# verify by looking at the template before removing.)

# Wait for new revision
sleep 60

# Verify normal operation
curl -s -H "Authorization: Bearer $RESEARCHER_TOKEN" "${CONTROL_PLANE_HOST}/api/health"
# Expected: 200.
```

> **Operational note:** between the env-var remove and restore, the dev control plane returns 503 to all submission attempts. Schedule ~10 min of dev downtime for this test, or run it during off-hours. The test does NOT affect already-running pipeline workflows or callbacks (those don't depend on `RAC_GH_PAT`).
>
> **Idempotency-key cleanup:** the `Idempotency-Key: $(uuidgen)` used above is a fresh uuid per invocation, so the cached 503 in the `idempotency_key` table is keyed to a value the system will never see again. No manual cleanup needed. (If you re-used a known key, you'd want to either invalidate the row via `DELETE FROM idempotency_key WHERE idempotency_key = '...'` after the test, or just let the 24h TTL age it out. Fresh-uuid avoids the issue entirely.)

**Verification:**

```bash
# AC7.5 — 503 was returned during the test window
echo "$RESPONSE" | head -1
# Expected: HTTP/1.1 503 Service Unavailable
echo "$RESPONSE" | grep -o '"code":"service_unavailable"'
# Expected: "code":"service_unavailable"
echo "$RESPONSE" | grep -o 'RAC_GH_PAT'
# Expected: RAC_GH_PAT (in the message).

# AC6.2 spot-check — no orphan submission row
# (already shown above).
```

**Commit:** None (operational; the env-var change is non-source).

<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Post-deploy validation sweep

**Verifies:** AC1.1, AC2.1-AC2.3, AC3.1, AC4.1-AC4.4, AC7.1-AC7.5 (re-confirms each via final greps + queries).

**Files:** none (verification only).

**Implementation:**

Run a single closing sweep that confirms the full system state. Capture output to `/tmp/phase5-final.log` and attach to the implementation-plan PR.

```bash
echo "=== Phase 5 final verification — $(date -u +%FT%TZ) ==="

echo ""
echo "--- AC1.1: FIC subject ---"
az ad app federated-credential list --id "$PIPELINE_APP_OBJECT_ID" \
  --query "[?name=='rac-pipeline-dev-gha'].subject" -o tsv
# Expected: repo:jdkruzr/rac-pipeline:environment:dev

echo ""
echo "--- AC2.x: four role assignments at per-resource scope ---"
az role assignment list --assignee "$SP_OBJECT_ID" \
  --query "[].{role:roleDefinitionName,scope:scope}" -o json | jq -c '.[]'
# Expected: 4 lines, each scope ending in /registries/<x>, /vaults/kv-rac-pl-<x>, or /containers/scan-artifacts.

echo ""
echo "--- AC3.1: rac-pipeline-dev app reg exists ---"
az ad app list --display-name 'rac-pipeline-dev' --query "length(@)" -o tsv
# Expected: 1

echo ""
echo "--- AC4.x: GH Environment + secrets + variables ---"
gh api "/repos/jdkruzr/rac-pipeline/environments/dev" --jq '.name'
gh secret list --env dev --repo jdkruzr/rac-pipeline | awk '{print $1}' | sort
gh variable list --env dev --repo jdkruzr/rac-pipeline | awk '{print $1, $2}' | sort

echo ""
echo "--- AC4.4: KV_NAME points to pipeline KV (not platform KV) ---"
gh variable get KV_NAME --env dev --repo jdkruzr/rac-pipeline | grep -E '^kv-rac-pl-' \
  && echo "AC4.4 OK" || echo "AC4.4 FAIL"

echo ""
echo "--- AC7 series: see captured workflow run logs from Task 6 + 503 capture from Task 7 ---"
echo "Task 6 workflow URL: <paste from manual run>"
echo "Task 7 503 response captured in: /tmp/phase5-task7-response.txt"

echo ""
echo "=== Phase 5 verification complete ==="
```

**Verification:** all above outputs match expectations. Any mismatch blocks Phase 5 completion.

**Commit:** None.

<!-- END_TASK_8 -->

---

## Phase 5 Done When

- All eight task verifications produce the stated expected outputs against the live `rg-rac-dev` and `jdkruzr/rac-pipeline` repo.
- At least one previously-stuck submission completes through the build-and-scan pipeline (AC7.1-AC7.4).
- The misconfigured-PAT case returns a clean 503 with no DB side-effects (AC7.5, AC6.2 in-prod replication).
- Post-task-7 cleanup has been run; the dev control plane is back to normal operation.
- The session log (`/tmp/phase5-deploy.log`) and the final-verification log (`/tmp/phase5-final.log`) are captured and ready to attach to the PR.
- `dev.bicepparam` updates are committed; staging/prod remain unchanged.
