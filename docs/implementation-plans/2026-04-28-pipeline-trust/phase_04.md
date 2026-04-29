# Pipeline Trust Implementation Plan — Phase 4: Runbook & Docs

**Goal:** Document the manual prerequisites and verification steps needed to take a fresh operator from "no app reg" to "first successful test dispatch" without external help. Add a one-paragraph contract note to `apps/control-plane/backend/CLAUDE.md` so future contributors know `dispatch_for_submission` is the single entry point and that callback secrets live in the dedicated pipeline KV.

**Architecture:** Pure documentation. No code changes, no infra changes, no test changes. The runbook section follows the existing §3 "Federated Identity Credentials for GHA" pattern in `bootstrap.md` (lines 89-120).

**Tech Stack:** Markdown, `az` CLI (`az ad app create`, `az ad app owner add`, `az ad sp show`), `gh` CLI (`gh api`, `gh secret set`, `gh variable set`), Azure portal links for fallback steps where CLI is awkward.

**Scope:** Phase 4 of 5. Docs-only. Phase 5 executes the runbook against a live tenant.

**Codebase verified:** 2026-04-28 by codebase-investigator. Existing `bootstrap.md` §3 (FIC for GHA) ends at line 120; the new section anchors after it. `apps/control-plane/backend/CLAUDE.md` exists and follows the project's CLAUDE.md style.

**External research findings:** None new. `az ad app create` / `az ad app owner add` / `gh secret set --env` / `gh variable set --env` are stable CLI surfaces well-documented in MS Learn and the GitHub CLI manual. Concrete invocations are inlined in the runbook.

---

## Acceptance Criteria Coverage

### pipeline-trust.AC4: GitHub repo dev Environment configured per runbook
- **pipeline-trust.AC4.5 Success:** `docs/runbooks/bootstrap.md` contains a "Pipeline Trust Setup" section with the manual app-reg + GH-Environment + bicepparam + verification steps.
  - *Phase 4 commitment:* the section is written, contains the eight ordered substeps below, and is cross-referenced from the design plan.

The other AC4 cases (AC4.1–AC4.4) verify the *runtime state of the GitHub repo + Azure tenant* — they're satisfied operationally in Phase 5 when the runbook is actually executed. Phase 4's job is to make sure the runbook *contains the right instructions*; Phase 5 verifies they work.

**Verifies in tests:** None. Verification = manual review + cross-references render correctly.

---

## Notes for the Implementor

- **Runbook style.** Mirror the existing `bootstrap.md` voice: imperative, numbered shell snippets, capture variables to env in early steps, reference them in later steps. Don't paraphrase commands ("create an app reg" vs `az ad app create --display-name ...`); show the exact CLI invocation.
- **Bidirectional cross-reference.** The new bootstrap section links to the design plan (`docs/design-plans/2026-04-28-pipeline-trust.md`) for context. The design plan's Phase 4 section already references `docs/runbooks/bootstrap.md` "Pipeline Trust Setup". After this phase, both pointers resolve.
- **Don't duplicate the design plan.** The runbook is a *playbook* — terse "do this, then this". Justification for *why* lives in the design plan; the runbook just links over.
- **Activate `ed3d-house-style:writing-for-a-technical-audience`** for the prose tone. Avoid AI tells (em-dashes, "elegantly", "comprehensive", etc.).

---

<!-- START_TASK_1 -->
### Task 1: Append "Pipeline Trust Setup" section to `docs/runbooks/bootstrap.md`

**Files:**
- Modify: `docs/runbooks/bootstrap.md` — insert new section as §3.5 (after the existing §3 "Federated Identity Credentials for GHA" which ends around line 120, and before the existing §4 "Bootstrap Key Vault" at line 122).

**Implementation:**

Insert the following section verbatim (with the exact heading level — `## 3.5 Pipeline Trust Setup`). The eight numbered substeps mirror the design plan's Phase 5 task ordering so the runbook can be executed top-to-bottom.

```markdown
## 3.5 Pipeline Trust Setup

This step provisions the per-environment Entra app registration that the
build-and-scan pipeline (`jdkruzr/rac-pipeline`) uses to authenticate to
this Azure subscription, plus the dedicated callback-secrets Key Vault.
See [`docs/design-plans/2026-04-28-pipeline-trust.md`](../design-plans/2026-04-28-pipeline-trust.md)
for the full architecture; this section is the playbook.

Substitute `<ENV>` with `dev` / `staging` / `prod` as appropriate. The
walkthrough below is for `dev`; staging and prod are codified in bicep
but not deployed in the current plan's scope (re-run this section when
those envs come online).

### 3.5.1 Create the per-env app registration

```bash
ENV=dev
GITHUB_OWNER=jdkruzr
GITHUB_REPO=rac-pipeline

# Create the app registration. Capture the appId and objectId.
APP_REG_JSON=$(az ad app create \
  --display-name "rac-pipeline-${ENV}" \
  --sign-in-audience AzureADMyOrg)

PIPELINE_APP_ID=$(echo "$APP_REG_JSON"   | jq -r .appId)
PIPELINE_APP_OBJECT_ID=$(echo "$APP_REG_JSON"   | jq -r .id)
PIPELINE_APP_UNIQUENAME=$(echo "$APP_REG_JSON" | jq -r .uniqueName)

echo "PIPELINE_APP_ID=$PIPELINE_APP_ID"               # GH secret AZURE_CLIENT_ID
echo "PIPELINE_APP_OBJECT_ID=$PIPELINE_APP_OBJECT_ID" # bicepparam pipelineAppUniqueNameDev source
echo "PIPELINE_APP_UNIQUENAME=$PIPELINE_APP_UNIQUENAME"

# Create the matching enterprise-app (service principal). Capture its objectId.
SP_OBJECT_ID=$(az ad sp create --id "$PIPELINE_APP_ID" --query id -o tsv)
echo "SP_OBJECT_ID=$SP_OBJECT_ID"  # bicepparam pipelineAppPrincipalIdDev source
```

> Save the four IDs (`PIPELINE_APP_ID`, `PIPELINE_APP_OBJECT_ID`, `PIPELINE_APP_UNIQUENAME`, `SP_OBJECT_ID`) — you'll paste each into a different place in the next steps.

### 3.5.2 Add the deploy SP as Owner of the new app reg

The bicep module `pipeline-identity.bicep` creates a child
`Microsoft.Graph/applications/federatedIdentityCredentials@v1.0`
resource under this app reg. Microsoft Graph requires the deploying
principal to be an Owner of the parent app to manage child credentials.

```bash
DEPLOY_SP_OBJECT_ID="<rac-infra-deploy SP object ID — see §1 of this runbook>"

az ad app owner add \
  --id "$PIPELINE_APP_OBJECT_ID" \
  --owner-object-id "$DEPLOY_SP_OBJECT_ID"

# Verify ownership was added
az ad app owner list --id "$PIPELINE_APP_OBJECT_ID" --query "[].id" -o tsv
# Expected: includes $DEPLOY_SP_OBJECT_ID.
```

### 3.5.3 Create the GitHub Environment + secrets + variables

```bash
TENANT_ID=$(az account show --query tenantId -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Create the GH Environment (idempotent — re-running is safe)
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${GITHUB_OWNER}/${GITHUB_REPO}/environments/${ENV}"

# Three secrets (azure auth)
gh secret set AZURE_CLIENT_ID       --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$PIPELINE_APP_ID"
gh secret set AZURE_TENANT_ID       --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$TENANT_ID"
gh secret set AZURE_SUBSCRIPTION_ID --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$SUBSCRIPTION_ID"

# Five variables (env-specific resource references). Pull these from the
# bicep deploy outputs for the matching env (Phase 5 / 'az deployment sub
# show ... --query properties.outputs ...').
ACR_NAME='<from bicep output acrLoginServer, name portion before .azurecr.io>'
ACR_LOGIN_SERVER='<from bicep output acrLoginServer>'
BLOB_ACCOUNT_URL='<from bicep output blobEndpoint, e.g. https://racdev....blob.core.windows.net/>'
KV_NAME='<from bicep output pipelineKvName — kv-rac-pl-...; NOT the platform KV>'
SEVERITY_GATE='high'  # or 'medium'/'low' per institutional preference

gh variable set ACR_NAME         --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$ACR_NAME"
gh variable set ACR_LOGIN_SERVER --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$ACR_LOGIN_SERVER"
gh variable set BLOB_ACCOUNT_URL --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$BLOB_ACCOUNT_URL"
gh variable set KV_NAME          --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$KV_NAME"
gh variable set SEVERITY_GATE    --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}" --body "$SEVERITY_GATE"
```

> **Critical: `KV_NAME` is the pipeline KV, not the platform KV.** The pipeline workflow fetches per-submission HMAC secrets from `kv-rac-pl-...-${ENV}`. If you set `KV_NAME` to the platform KV (`kv-rac-...`), the workflow will get a 403 because the pipeline app reg has no role on the platform KV (by design — see AC2.2 in the design plan).

### 3.5.4 Update the env's bicepparam with the captured IDs

Edit `infra/environments/${ENV}.bicepparam`. Set the four pipeline-trust
params with the values captured in §3.5.1:

```bicep
param deployPipelineKv = true
param deployPipelineIdentity = true
param pipelineAppUniqueNameDev = '<PIPELINE_APP_UNIQUENAME from §3.5.1>'
param pipelineAppPrincipalIdDev = '<SP_OBJECT_ID from §3.5.1>'
param pipelineAppClientIdDev = '<PIPELINE_APP_ID from §3.5.1>'
```

> Note: `pipelineAppUniqueNameDev` is the Microsoft Graph **uniqueName** of
> the app reg, not its display name. Bicep's `Microsoft.Graph/applications`
> resource uses uniqueName as the lookup key for `existing` references.
> `pipelineAppPrincipalIdDev` is the **enterprise-app SP's objectId**, not
> the app reg's objectId — Azure RBAC role assignments target the SP.

### 3.5.5 Pass-2 deploy with the gates flipped on

```bash
az deployment sub create \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/${ENV}.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID"
```

This deploys the new pipeline KV (`kv-rac-pl-...`), the FIC under
the app reg, and the four resource-scoped role assignments.

### 3.5.6 Wait for RBAC propagation

```bash
echo "Waiting 5 minutes for Azure RBAC role assignments to propagate..."
sleep 300
```

Azure RBAC is eventually consistent (~5 min). Skipping this wait causes
the first test dispatch's "Azure login (OIDC)" or "Fetch callback secret"
step to fail with a 403 even though everything is configured correctly.

### 3.5.7 Verify the trust setup

```bash
# Verify the FIC was created
az ad app federated-credential list \
  --id "$PIPELINE_APP_OBJECT_ID" \
  --query "[].{name:name,subject:subject,audiences:audiences}" -o json
# Expected: one entry with name=rac-pipeline-${ENV}-gha,
# subject=repo:${GITHUB_OWNER}/${GITHUB_REPO}:environment:${ENV},
# audiences=["api://AzureADTokenExchange"].

# Verify the four role assignments
az role assignment list \
  --assignee "$SP_OBJECT_ID" \
  --query "[].{role:roleDefinitionName,scope:scope}" -o table
# Expected: four rows: AcrPull, AcrPush (both on the env ACR resource);
# Key Vault Secrets User (on the pipeline KV — kv-rac-pl-...);
# Storage Blob Data Contributor (on the env's scan-artifacts blob container).
# All four scopes are per-resource, never resource-group or subscription.

# Verify the GH Environment is configured
gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}/environments/${ENV}"
gh secret list   --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}"
gh variable list --env "${ENV}" --repo "${GITHUB_OWNER}/${GITHUB_REPO}"
```

### 3.5.8 First test dispatch (admin retry on a stuck submission)

Pick a submission stuck in `awaiting_scan` from before this plan
landed (or create a new one — the original-create flow now mints a real
secret too). Then:

```bash
SUBMISSION_ID='<uuid of the stuck submission>'
ADMIN_TOKEN='<admin OIDC bearer; see §6 of this runbook for token-acquire>'
CONTROL_PLANE_HOST='<your env, e.g. https://rac-dev.rac.checkwithscience.com>'

curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "${CONTROL_PLANE_HOST}/api/admin/submissions/${SUBMISSION_ID}/dispatch/retry"
# Expected: 200 with body {submission_id, callback_url, dispatched_at}.
```

Then watch the `jdkruzr/rac-pipeline` Actions tab for the new workflow
run. Confirm:

1. `Azure login (OIDC)` step exits 0.
2. `Fetch callback secret` step succeeds (reads `rac-pipeline-cb-${SUBMISSION_ID}` from `kv-rac-pl-...-${ENV}`).
3. Build + scan completes.
4. The control plane receives a callback and the submission FSM advances out of `awaiting_scan` to either `awaiting_research_review` or `severity_gate_failed`.

If the workflow fails at "Azure login (OIDC)" with `AADSTS70021` or
`AADSTS700213`, the FIC subject doesn't match the workflow's
`environment:` declaration. Re-check that `bicep` deployed the FIC with
the correct `${racEnv}` and that the workflow declares
`environment: ${ENV}` on the build job.

If it fails at "Fetch callback secret" with a 403, RBAC hasn't
propagated yet (wait another 5 min) OR `KV_NAME` is wrong (verify §3.5.3).
```

**Verification:**

```bash
cd /home/sysop/rac

# Section is present, named correctly, and references the design plan
grep -n "## 3.5 Pipeline Trust Setup" docs/runbooks/bootstrap.md
# Expected: one match.

grep -n "docs/design-plans/2026-04-28-pipeline-trust.md" docs/runbooks/bootstrap.md
# Expected: at least one match (the new section's intro link).

# All eight substeps exist
grep -nE "### 3\.5\.[1-8] " docs/runbooks/bootstrap.md
# Expected: eight matches, in order.

# No accidental hard-coded paths or secrets
grep -nE "ghp_|sk_|RAC_PG_ADMIN_PASSWORD=|password=" docs/runbooks/bootstrap.md | grep -v "RAC_PG_ADMIN_PASSWORD\$" || true
# Expected: no leaked secrets.
```

**Commit:**

```bash
cd /home/sysop/rac
git add docs/runbooks/bootstrap.md
git commit -m "docs(runbooks/bootstrap): add Pipeline Trust Setup section (3.5)

Eight ordered substeps to take a fresh operator from no app reg to a
verified end-to-end test dispatch. Covers az ad app create + Owner
add, GH Environment + 3 secrets + 5 variables, bicepparam wiring,
Pass-2 deploy, RBAC-propagation wait, and verification commands.

Cross-references docs/design-plans/2026-04-28-pipeline-trust.md."
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Add a contract note to `apps/control-plane/backend/CLAUDE.md`

**Files:**
- Modify: `apps/control-plane/backend/CLAUDE.md` — append a one-paragraph addendum to the "Key contracts" section (between the "Approvals" and "Provisioning" bullets, OR at the bottom of "Key contracts" — whichever location reads more naturally; depends on the freshness of the file at execution time).

**Implementation:**

Add the following bullet to the "Key contracts" section:

```markdown
- **Pipeline dispatch.** `services/pipeline_dispatch/dispatch_helper.py::dispatch_for_submission` is the single entry point for both the submission-created and admin-retry flows. The helper mints a per-submission HMAC callback secret in the dedicated pipeline KV (`settings.pipeline_kv_uri`, env var `RAC_PIPELINE_KV_URI`), builds the dispatch payload via `payload.build_dispatch_payload` (Functional Core), and POSTs `repository_dispatch`. Missing `gh_pat` or `pipeline_kv_uri` raises `DispatchUnavailableError` BEFORE any I/O — the API layer maps it to a 503 response, and the idempotency middleware caches the 503 like any other status. Callback secrets live in `kv-rac-pl-${env}` only, never the platform KV (`kv-rac-${unique}-${env}`); the strict separation is part of the per-env pipeline app reg's RBAC isolation.
```

**Verification:**

```bash
cd /home/sysop/rac
grep -n "dispatch_for_submission" apps/control-plane/backend/CLAUDE.md
# Expected: at least one match.

grep -n "kv-rac-pl-" apps/control-plane/backend/CLAUDE.md
# Expected: at least one match (the strict separation note).
```

**Commit:**

```bash
cd /home/sysop/rac
git add apps/control-plane/backend/CLAUDE.md
git commit -m "docs(control-plane/CLAUDE.md): note dispatch contract + pipeline KV

Captures: dispatch_for_submission is the single entry point for
both the original-create and admin-retry flows; callback secrets
live in the dedicated pipeline KV (RAC_PIPELINE_KV_URI), never the
platform KV; missing config raises DispatchUnavailableError before
I/O, mapping to a 503 the idempotency middleware can replay."
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Verify cross-references resolve

**Files:** none (verification only).

**Implementation:**

```bash
cd /home/sysop/rac

# 1. Bootstrap runbook references the design plan
grep -A1 "## 3.5 Pipeline Trust Setup" docs/runbooks/bootstrap.md | grep -q "design-plans/2026-04-28-pipeline-trust.md"
# Expected: matches.

# 2. Design plan references the runbook
grep -n "docs/runbooks/bootstrap.md" docs/design-plans/2026-04-28-pipeline-trust.md
# Expected: at least one match.

# 3. CLAUDE.md was updated
grep -c "dispatch_for_submission" apps/control-plane/backend/CLAUDE.md
# Expected: >= 1.

# 4. Markdown renders cleanly (optional but recommended)
# If you have prettier / markdownlint locally:
# npx prettier --check docs/runbooks/bootstrap.md
# npx markdownlint docs/runbooks/bootstrap.md

# 5. Working tree clean
git status --short docs/ apps/control-plane/backend/CLAUDE.md
# Expected: empty.
```

If a cross-reference is missing, fix and re-commit. Phase 4 is complete only when all four checks pass.

**Commit:** None (verification step).
<!-- END_TASK_3 -->

---

## Phase 4 Done When

- `docs/runbooks/bootstrap.md` contains a "## 3.5 Pipeline Trust Setup" section with eight ordered substeps (3.5.1 — 3.5.8).
- `apps/control-plane/backend/CLAUDE.md` contains a "Pipeline dispatch" bullet in its Key contracts section naming `dispatch_for_submission` and the pipeline KV separation invariant.
- Cross-reference between bootstrap.md ↔ design plan resolves both ways.
- Working tree clean; both commits on the implementation branch.
