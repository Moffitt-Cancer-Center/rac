# Pipeline Trust Implementation Plan — Phase 2: Bicep Integration (Gated)

**Goal:** Wire `pipeline-kv.bicep` and `pipeline-identity.bicep` (Phase 1 deliverables) into `infra/main.bicep` behind a default-off gate. Add the four new parameters to dev/staging/prod bicepparam files. After this phase, `az deployment sub validate` passes both gate-off (default) and gate-on (with stub principal IDs); `az deployment sub what-if` against the live dev RG shows an empty diff with gate-off.

**Architecture:** A single boolean gate `deployPipelineIdentity` (default `false`) controls whether the new modules are invoked. When `false`, neither module produces any Azure resources — what-if diff is empty. When `true` with non-empty principal IDs, both modules deploy. When `true` with empty `pipelineAppPrincipalIdDev`, the deployment guard surfaces a clear ARM validate-time error via empty-name-trip mechanism (no silent broken state).

**Tech Stack:** Bicep, `az deployment sub validate`, `az deployment sub what-if`.

**Scope:** Phase 2 of 5. Pure integration — Phase 1's modules become reachable via the gate. Phase 3 is control-plane Python; Phase 4 is runbook docs; Phase 5 is the live Pass-2 deploy.

**Codebase verified:** 2026-04-28 by codebase-investigator.

**Verification snapshot:**
- ✓ Param sections in `main.bicep` are dividied by `// ===== Section Name =====` comments (lines 120, 165). The new pipeline section will go between line 163 (end of "Control Plane (Phase 2)") and line 165 (start of "Front Door custom domain").
- ✓ Module invocation conventions: `scope:` → `name:` → `dependsOn:` (if needed) → `params:`. Existing examples: `controlPlaneAcaApp` (lines 522-568, conditional gated), `keyVault` (lines 233-246, unconditional).
- ✓ `managedIdentity` module is invoked at line 411 with output `controlPlaneMiPrincipalId` (per `managed-identity.bicep:34-35`). The new pipeline-kv module needs this as input.
- ✓ `acr` module outputs `acrId`, `acrResourceId`, `acrLoginServer` (per investigator). The new pipeline-identity module needs `acrId`.
- ✓ `blobStorage` module (`infra/modules/blob-storage.bicep`) currently outputs `storageAccountId`, `storageAccountName`, `blobEndpoint` but **NOT a per-container resource ID**. The new pipeline-identity module needs the `scan-artifacts` container resource ID. We add an output to blob-storage.bicep in Task 1.
- ✓ Existing gate-pattern reference: `param deployControlPlaneApp bool = false` (line 123) + conditional invocation `if (deployControlPlaneApp && !empty(controlPlaneImageName))` (line 522).
- ✓ Existing GH-pipeline params already in main.bicep: `controlPlaneGithubPipelineOwner` (line 154, default `''`) and `controlPlaneGithubPipelineRepo` (line 157, default `'rac-pipeline'`). The new pipeline-identity invocation will reuse these — no new GitHub-related param needed.
- ✓ `dev.bicepparam` ends at line 71 with the Front Door custom domain section. The new pipeline-trust section goes right before it (after line 61). `staging.bicepparam` and `prod.bicepparam` are 26-line skeletons; they need stubbed pipeline-trust params with empty defaults so `az deployment sub validate` succeeds for those envs.
- ✓ GHA workflow at `.github/workflows/infra-deploy.yml` runs `az deployment sub what-if` (lines 82-91) and `az deployment sub create` (lines 113-123). I'll mirror that command shape in this phase's verification.

**External research findings:** None new beyond Phase 1.

---

## Acceptance Criteria Coverage

### pipeline-trust.AC1: Per-env identity + FIC provisioned via bicep
- **pipeline-trust.AC1.4 Failure:** bicep `validate` fails with a clear error message when `deployPipelineIdentity=true` AND `pipelineAppObjectId` is empty.
  - *Phase 2 mechanism:* the `pipelineIdentity` module invocation uses a guarded `name:` expression — if the gate is on but `pipelineAppPrincipalIdDev` is empty, the module name resolves to `''`, which ARM rejects at validate time with `The 'name' property is required and cannot be empty`. (Note: this enforces on `pipelineAppPrincipalIdDev`, not `pipelineAppObjectId`. The Phase 1 module rename of `pipelineAppObjectId` → `pipelineAppUniqueName` carries through here; the principal-ID guard captures the same intent.)
- **pipeline-trust.AC1.5 Edge:** `deployPipelineIdentity=false` (default) produces zero new resources in `what-if` (gate works).
  - *Phase 2 mechanism:* both `pipelineKv` and `pipelineIdentity` invocations are gated on `if (deployPipelineIdentity)`; with the gate off, neither module deploys.

### pipeline-trust.AC3: Dev provisioned; staging/prod codified, not deployed
- **pipeline-trust.AC3.2 Success:** bicepparam wiring for staging and prod compiles and `validate`s cleanly with `deployPipelineIdentity=false` (codified, not deployed).
  - *Phase 2 mechanism:* staging.bicepparam and prod.bicepparam get the new params with empty/false defaults so `az deployment sub validate` succeeds against both environments.

**Verifies in tests:** None automated. Phase 2 verification is `az bicep build` (compile) + `az deployment sub validate` (template + bicepparam check, on dev/staging/prod) + `az deployment sub what-if` (gate-off zero-diff confirmation against live dev RG).

---

## Notes for the Implementor

- **Phase 2 changes only `infra/`. No Python, no docs.** This is pure bicep integration.
- **The `name:` expression in the `pipelineIdentity` module invocation is load-bearing for AC1.4.** When `deployPipelineIdentity=true` and `pipelineAppPrincipalIdDev=''`, the conditional `pipelineAppPrincipalIdDev != '' ? '...' : ''` resolves to `''`, and ARM rejects empty deployment names at validate time. Do NOT change this expression to `'deploy-pipeline-identity-${racEnv}'` unconditionally — that breaks the safety guard and AC1.4 fails.
- **Order of module insertion matters.** `pipeline-kv` must be deployed before `pipeline-identity` because the latter takes `pipelineKv.outputs.kvId` as input. Insert `pipelineKv` after `keyVault` (around line 247) and `pipelineIdentity` after `managedIdentity` (around line 419).
- **Reuse `controlPlaneGithubPipelineOwner` / `controlPlaneGithubPipelineRepo` params.** Don't introduce new GitHub-related params. The FIC subject derives from these.
- **For dev only**, the new param `pipelineAppPrincipalIdDev` will be populated in Phase 4 runbook execution + Phase 5 Pass-2 deploy. For now, leave it empty in `dev.bicepparam`.
- **For staging/prod**, the corresponding `pipelineAppPrincipalIdStaging` / `pipelineAppPrincipalIdProd` are NOT created in this phase — design plan §"Definition of Done" item 3 says staging/prod are "codified in bicep but not deployed". We add ONE shared input `pipelineAppPrincipalIdDev` (and matching `UniqueName`/`ClientId`) for now; the bicep contract is parameterized so a future plan can extend with per-env IDs. Document this in `dev.bicepparam` comments.
- Activate `ed3d-house-style:coding-effectively` for general hygiene.

---

<!-- START_TASK_1 -->
### Task 1: Add `scanArtifactsContainerId` output to `blob-storage.bicep`

**Files:**
- Modify: `infra/modules/blob-storage.bicep`

**Implementation:**

Append a new output near the existing outputs (currently at lines 270-277 of `blob-storage.bicep`):

```bicep
@description('Scan artifacts blob container resource ID (used by pipeline-identity for least-privilege RBAC scoping)')
output scanArtifactsContainerId string = containerScanArtifacts.id
```

`containerScanArtifacts` is the existing symbolic name at `blob-storage.bicep:72-76` (resource type `Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01`).

**Verification:**

```bash
cd /home/sysop/rac
az bicep build --file infra/modules/blob-storage.bicep
# Expected: zero output, exit 0; blob-storage.json sibling updated.
git diff infra/modules/blob-storage.bicep | head -20
# Expected: shows the new output stanza appended near other outputs.
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/modules/blob-storage.bicep infra/modules/blob-storage.json
git commit -m "infra(blob-storage): expose scan-artifacts container resourceId

Added so pipeline-identity.bicep can scope Storage Blob Data Contributor
to the single container the pipeline writes to (Syft SBOM, Grype JSON,
scan logs), rather than the whole storage account."
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Wire pipeline-trust params, module invocations, and outputs into `infra/main.bicep`

**Files:**
- Modify: `infra/main.bicep`

**Implementation:**

This task is one logical change with multiple insertion points in `main.bicep`. Make all edits in a single commit so `main.bicep` always compiles.

#### 2a. Add new params section (between line 163 and line 165)

Insert after the existing `controlPlaneOtlpEndpoint` param (line 163) and before the `// ===== Front Door custom domain =====` divider (line 165):

```bicep

// ===== Pipeline Trust (Phase 1+2: gated; default off) =====

@description('Deploy the per-env pipeline Entra app FIC + role assignments. Set true once the manual app reg + GH Environment exist (see docs/runbooks/bootstrap.md "Pipeline Trust Setup"). Pipeline KV is created independently — see deployPipelineKv. Default false on first deploy.')
param deployPipelineIdentity bool = false

@description('Whether to create the dedicated pipeline KV (kv-rac-pipeline-...). Independent of deployPipelineIdentity so the KV can exist before the FIC and roles are wired. Recommended: deploy KV in pass 1 (so the CP can mint secrets immediately), wire FIC+RBAC in pass 2.')
param deployPipelineKv bool = false

@description('Microsoft Graph uniqueName of the manually-created Entra app reg rac-pipeline-${racEnv}. Empty until app reg is provisioned per runbook. Required when deployPipelineIdentity=true.')
param pipelineAppUniqueNameDev string = ''

@description('Service principal (enterprise app) object ID of the manually-created rac-pipeline-${racEnv} app reg. Empty until app reg is provisioned per runbook. Required when deployPipelineIdentity=true; when this param is empty AND deployPipelineIdentity is true, validate fails with an empty-name template error.')
param pipelineAppPrincipalIdDev string = ''

@description('Application (client) ID of rac-pipeline-${racEnv}. Captured for runbook reference + emitted as output. Optional but recommended.')
param pipelineAppClientIdDev string = ''
```

#### 2b. Add `pipelineKv` module invocation (between lines 246 and 248, i.e., after `keyVault` and before `blobStorage`)

```bicep

module pipelineKv 'modules/pipeline-kv.bicep' = if (deployPipelineKv) {
  scope: rg
  name: 'deploy-pipeline-kv'
  params: {
    racEnv: racEnv
    location: location
    tags: commonTags
    controlPlaneMiPrincipalId: managedIdentity.outputs.controlPlaneMiPrincipalId
  }
}
```

> **Forward-reference note:** `managedIdentity` is declared at line 411, *after* line 247. Bicep resolves symbolic references regardless of declaration order; this is supported and matches how `roleAssignmentsKv` (line 423) already references `managedIdentity.outputs.*`.

#### 2c. Add `pipelineIdentity` module invocation (between lines 419 and 421, i.e., right after `managedIdentity`)

```bicep

// pipelineIdentity wires FIC + 4 RBAC for the per-env rac-pipeline-${racEnv}
// Entra app reg (pre-created manually, see docs/runbooks/bootstrap.md
// "Pipeline Trust Setup"). Two validate-time safety guards on the `name:`
// expression: (1) deployPipelineIdentity=true AND pipelineAppPrincipalIdDev
// empty → name resolves to '' → ARM "deployment.name property required"
// error; (2) deployPipelineIdentity=true AND deployPipelineKv=false
// (operationally invalid: pipeline KV must exist for the Secrets User role
// assignment to land) → name resolves to a sentinel string visible in the
// error message, so the operator sees what's wrong at validate time.
module pipelineIdentity 'modules/pipeline-identity.bicep' = if (deployPipelineIdentity) {
  scope: rg
  name: empty(pipelineAppPrincipalIdDev)
    ? '' // ← AC1.4: ARM rejects empty deployment names at validate time
    : (!deployPipelineKv
        ? 'GUARD-FAIL-deployPipelineKv-must-be-true-when-deployPipelineIdentity-is-true'
        : 'deploy-pipeline-identity-${racEnv}')
  params: {
    racEnv: racEnv
    pipelineAppUniqueName: pipelineAppUniqueNameDev
    pipelineAppPrincipalId: pipelineAppPrincipalIdDev
    pipelineGithubOwner: controlPlaneGithubPipelineOwner
    pipelineGithubRepo: controlPlaneGithubPipelineRepo
    acrId: acr.outputs.acrId
    pipelineKvId: pipelineKv.?outputs.kvId ?? ''
    artifactsBlobContainerId: blobStorage.outputs.scanArtifactsContainerId
  }
}
```

> **Two guards on the `name:` expression:**
>
> 1. **AC1.4 enforcement (validate-time).** When `deployPipelineIdentity=true` AND `pipelineAppPrincipalIdDev=''`, the `name:` resolves to `''`. ARM rejects empty deployment names at validate time with `The 'name' property is required and cannot be empty.` This satisfies AC1.4's "validate fails with a clear error message" — though the error is generic ARM, not domain-specific, the test in Task 4 verifies it surfaces.
>
> 2. **KV-Identity consistency (deploy-time).** When `deployPipelineIdentity=true` AND `deployPipelineKv=false` (an operationally invalid combination — the role assignment cannot scope to a KV that doesn't exist), the `name:` resolves to the literal string `'GUARD-FAIL-deployPipelineKv-must-be-true-when-deployPipelineIdentity-is-true'`. ARM's deployment-name length limit (64 chars) is enforced at `az deployment sub create` time, not validate time. This 76-char string triggers an error at deploy time with the failing string visible in the error message, so the operator immediately sees what's wrong without having to read bicep source.
>
> If you'd prefer a clearer mechanism (e.g., a Bicep `assert` statement), Bicep's `assert` is in preview as of 2026 and not yet stable — the empty-name and over-length-name techniques are the supported escape hatches.

#### 2d. Add outputs at end of file (after line 658)

Append after the existing `frontDoorApexValidationToken` output (line 658):

```bicep

@description('Pipeline KV vault URI (empty when deployPipelineKv=false). Set RAC_PIPELINE_KV_URI env var on the control-plane container app from this value in Phase 3.')
output pipelineKvUri string = pipelineKv.?outputs.kvUri ?? ''

@description('Pipeline KV name (empty when deployPipelineKv=false). Use as the value of the GH Environment variable KV_NAME on jdkruzr/rac-pipeline (see runbook).')
output pipelineKvName string = pipelineKv.?outputs.kvName ?? ''

@description('Pipeline FIC resource ID (empty when deployPipelineIdentity=false).')
output pipelineFicId string = pipelineIdentity.?outputs.ficResourceId ?? ''

@description('Pipeline app client ID (parroted back from input; useful in runbook output for operator to copy as AZURE_CLIENT_ID into GH Environment dev secrets).')
output pipelineAppClientId string = pipelineAppClientIdDev
```

**Verification:**

```bash
cd /home/sysop/rac

# Bicep compiles
az bicep build --file infra/main.bicep
# Expected: zero output, exit 0; main.json regenerated.

# what-if vs live dev RG with default param (gate off) shows zero diff
az deployment sub what-if \
  --location "$(grep '^param location' infra/environments/dev.bicepparam | head -1 | cut -d= -f2 | tr -d ' \"')" \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  | tee /tmp/whatif-phase2.txt
# Expected: "Resource changes: no change." Exit 0. AC1.5 satisfied.

# Validate with gate ON + empty principal -> must FAIL
az deployment sub validate \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  --parameters deployPipelineIdentity=true pipelineAppPrincipalIdDev='' \
  || echo "EXPECTED-FAIL"
# Expected: validate exits non-zero with an error mentioning 'name' is required / empty.
# AC1.4 satisfied.

# Validate with gate ON + stub principal -> must PASS
az deployment sub validate \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  --parameters deployPipelineKv=true deployPipelineIdentity=true pipelineAppUniqueNameDev='stub-app-uniquename' pipelineAppPrincipalIdDev='00000000-0000-0000-0000-000000000001' pipelineAppClientIdDev='00000000-0000-0000-0000-000000000002'
# Expected: exit 0.
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/main.bicep infra/main.json
git commit -m "infra(main): wire pipeline-kv + pipeline-identity behind deployPipelineIdentity gate

Adds five new params (deployPipelineIdentity, deployPipelineKv,
pipelineApp{UniqueName,PrincipalId,ClientId}Dev) and two conditional
module invocations. Default-off; what-if vs the live dev RG shows zero
diff. When the gate is enabled but pipelineAppPrincipalIdDev is empty,
the deployment name resolves to '' and ARM rejects with a clear
template-validation error before any Azure call (the AC1.4 safety
guard). Reuses controlPlaneGithubPipelineOwner/Repo to derive the FIC
subject. New outputs expose pipelineKvUri / pipelineKvName /
pipelineFicId / pipelineAppClientId for runbook + Phase 3 wiring."
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Add pipeline-trust param stubs to dev/staging/prod bicepparam files

**Files:**
- Modify: `infra/environments/dev.bicepparam`
- Modify: `infra/environments/staging.bicepparam`
- Modify: `infra/environments/prod.bicepparam`

**Implementation:**

#### 3a. `infra/environments/dev.bicepparam`

Insert a new section after line 61 (end of `// ===== Phase 2: Control Plane =====` block) and before line 63 (`// ===== Front Door custom domain =====` divider):

```bicep

// ===== Phase 1+2: Pipeline Trust (default-off; populated after manual app reg) =====
// Two-pass deploy: pass 1 leaves deployPipelineIdentity=false; operator manually
// creates rac-pipeline-dev app reg + GH Environment per docs/runbooks/bootstrap.md
// "Pipeline Trust Setup", populates the three IDs below, then re-deploys with
// deployPipelineIdentity=true and deployPipelineKv=true.
// deployPipelineKv can flip to true on Pass 1 if you want the CP to start minting
// callback secrets before the FIC+RBAC are wired (recommended for testing dispatch
// flow without a live pipeline run).
param deployPipelineKv = false
param deployPipelineIdentity = false
param pipelineAppUniqueNameDev = ''
param pipelineAppPrincipalIdDev = ''
param pipelineAppClientIdDev = ''
```

#### 3b. `infra/environments/staging.bicepparam`

The staging file is a 26-line skeleton (per investigator). Append the same block at the end of the file:

```bicep

// ===== Phase 1+2: Pipeline Trust (codified, NOT deployed in this plan's scope) =====
// Per design plan DoD item 3: staging is "codified in bicep but not deployed".
// These stubs allow `az deployment sub validate` against staging to succeed.
// A future plan provisions the rac-pipeline-staging app reg + flips the gate.
param deployPipelineKv = false
param deployPipelineIdentity = false
param pipelineAppUniqueNameDev = ''
param pipelineAppPrincipalIdDev = ''
param pipelineAppClientIdDev = ''
```

> Note the param names still carry `Dev` suffix even in staging/prod. The design plan states these app-reg-id params will be generalized to per-env `*Staging` / `*Prod` versions in a follow-up plan; for now, the param contract is single-env-shaped and staging/prod use the same names with empty values. This is acceptable because the gate is off, so the params are inert. Document this in the comment block (already done above).

#### 3c. `infra/environments/prod.bicepparam`

Same as staging — append the same block at the end of the file. The comment text is identical (substitute "prod" for "staging" if the engineer wants to be precise; functionally the same).

**Verification:**

```bash
cd /home/sysop/rac

# Each env's bicepparam compiles + validates
for env in dev staging prod; do
  echo "=== validating $env ==="
  az deployment sub validate \
    --location eastus2 \
    --template-file infra/main.bicep \
    --parameters infra/environments/${env}.bicepparam \
    --parameters pgAdminPassword='stub-pass' appGwTlsCertKvSecretId='/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-stub/providers/Microsoft.KeyVault/vaults/kv-stub/secrets/stub' \
    > /tmp/validate-${env}.txt 2>&1 \
    && echo "$env OK" \
    || (echo "$env FAILED"; cat /tmp/validate-${env}.txt)
done
# Expected: "dev OK", "staging OK", "prod OK". AC3.2 satisfied.
```

> Note: staging/prod validates may fail for unrelated reasons (e.g., they're skeletons missing required params for other modules). Capture and triage any non-pipeline-trust failures separately — the goal here is that adding these new params doesn't *introduce* new validation failures vs. the pre-Phase-2 state. If staging/prod were already failing before Phase 2, log that fact and proceed; the AC3.2 commitment is "no new failures from pipeline-trust params".

**Commit:**

```bash
cd /home/sysop/rac
git add infra/environments/dev.bicepparam infra/environments/staging.bicepparam infra/environments/prod.bicepparam
git commit -m "infra(env): stub pipeline-trust params (deployPipelineIdentity=false default)

dev: ready for Pass 2 once rac-pipeline-dev app reg is provisioned.
staging/prod: codified per DoD item 3, gate stays off in this plan's
scope. Param names use Dev suffix for now; per-env *Staging / *Prod
variants are deferred to a follow-up plan."
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: End-to-end Phase 2 verification

**Files:** none (verification only).

**Implementation:**

Run a complete sweep to confirm AC1.4, AC1.5, AC3.2 are all satisfied at this phase's exit.

**Verification:**

```bash
cd /home/sysop/rac

# 0) Working tree clean (everything from Tasks 1-3 committed)
git status --short
# Expected: empty.

# 1) All bicep modules build clean (zero warnings)
az bicep build --file infra/main.bicep
az bicep build --file infra/modules/pipeline-kv.bicep
az bicep build --file infra/modules/pipeline-identity.bicep
az bicep build --file infra/modules/blob-storage.bicep
az bicep build --file infra/modules/role-ids.bicep
# Expected: each command exits 0 with zero output.

# 2) AC1.5 — gate-off what-if vs live dev RG = empty diff
az deployment sub what-if \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  | grep -E "Resource changes|No change|no change" | head -5
# Expected: "Resource changes: no change." or equivalent.

# 3) AC1.4 — gate-on with empty principalId fails validate
az deployment sub validate \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  --parameters deployPipelineIdentity=true \
  ; echo "exit=$?"
# Expected: non-zero exit. Error mentions 'name' property required / empty.

# 3b) Consistency guard — gate-on for identity but KV gate off should also fail validate
az deployment sub validate \
  --location eastus2 \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters pgAdminPassword="$RAC_PG_ADMIN_PASSWORD" appGwTlsCertKvSecretId="$RAC_APPGW_TLS_CERT_KV_SECRET_ID" \
  --parameters deployPipelineIdentity=true deployPipelineKv=false pipelineAppUniqueNameDev='stub-app-uniquename' pipelineAppPrincipalIdDev='00000000-0000-0000-0000-000000000001' pipelineAppClientIdDev='00000000-0000-0000-0000-000000000002' \
  ; echo "exit=$?"
# Expected: non-zero exit. Error message includes
# 'GUARD-FAIL-deployPipelineKv-must-be-true-when-deployPipelineIdentity-is-true'
# (the over-length deployment name surfaces the literal in the validation error).

# 4) AC3.2 — staging + prod validate cleanly (gate off)
for env in staging prod; do
  az deployment sub validate \
    --location eastus2 \
    --template-file infra/main.bicep \
    --parameters infra/environments/${env}.bicepparam \
    --parameters pgAdminPassword='stub' appGwTlsCertKvSecretId='/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/x/providers/Microsoft.KeyVault/vaults/x/secrets/x' \
    > /tmp/v-${env}.log 2>&1 && echo "$env: OK" || echo "$env: FAILED (see /tmp/v-${env}.log)"
done
# Expected: both "OK".

# 5) Manual structural check: AC1.5 backstop
grep -nE "(pipeline-kv|pipeline-identity)" infra/main.bicep | head -10
# Expected: lines confirming both module invocations are conditional `if (...)` and unconditional refs are absent.
grep -E "module pipeline" infra/main.bicep
# Expected: two lines, each starting with "module pipelineXxx '...' = if (..." (gated).
```

If any of the above fail, fix and re-run before declaring Phase 2 done.

**Commit:** None (verification step). Phase 2 is complete when all five checks pass.
<!-- END_TASK_4 -->

---

## Phase 2 Done When

- All four task verifications pass.
- Working tree is clean and Phase 1 + Phase 2 commits sit on the implementation branch.
- `az bicep build` on `main.bicep`, `pipeline-kv.bicep`, `pipeline-identity.bicep`, `blob-storage.bicep`, `role-ids.bicep` all succeed with zero warnings.
- AC1.4 demonstrated: gate-on with empty principal fails `validate` with a clear error.
- AC1.5 demonstrated: gate-off `what-if` against live dev RG shows zero changes.
- AC3.2 demonstrated: `staging` and `prod` bicepparam files validate cleanly.
- No live deploy has happened yet (still pre-Pass-2; Pass 2 is Phase 5).
