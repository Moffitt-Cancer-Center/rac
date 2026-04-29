# Pipeline Trust Implementation Plan — Phase 1: Bicep Groundwork

**Goal:** Create two new bicep modules (`pipeline-kv.bicep`, `pipeline-identity.bicep`) and extend `role-ids.bicep` with two new role GUID constants. No wiring into `main.bicep`, no live infra change.

**Architecture:** Extend the existing `role-ids.bicep` registry with `acrPush` and `storageBlobDataContributor` GUIDs. Add a new module that creates a dedicated per-env callback-secrets Key Vault (`kv-rac-pl-${racEnv}`) with public access enabled (GitHub-Actions-reachable) and Secrets-User RBAC mode. Add a second module that, given a pre-existing manually-created Entra app reg, creates one `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0` plus four resource-scoped `Microsoft.Authorization/roleAssignments@2022-04-01` (AcrPull, AcrPush, KV Secrets User, Storage Blob Data Contributor).

**Tech Stack:** Bicep, Azure RBAC built-in roles, Microsoft Graph Bicep extension (first use in this codebase).

**Scope:** Phase 1 of 5. Modules only — no `main.bicep` integration (Phase 2), no control-plane changes (Phase 3), no docs (Phase 4), no live deploy (Phase 5).

**Codebase verified:** 2026-04-28 by codebase-investigator.

**Verification snapshot:**
- ✓ `infra/modules/role-ids.bicep` exists (lines 1-11). Already exports `acrPull` (line 10) and `keyVaultSecretsUser` (line 6). Missing: `acrPush`, `storageBlobDataContributor`.
- ✓ `infra/modules/pipeline-kv.bicep` and `infra/modules/pipeline-identity.bicep` do NOT exist (confirmed absent).
- ✓ Existing platform KV pattern (`infra/modules/key-vault.bicep:27-52`) uses `enableRbacAuthorization: true`, parameterized `softDeleteRetentionInDays` (7-90), parameterized `enablePurgeProtection`, `publicNetworkAccess: 'Disabled'` + private endpoint. Pipeline KV intentionally diverges on the network setting (must be reachable from GH Actions runners).
- ✓ Existing role-assignment pattern (`infra/modules/role-assignments.bicep:42-50`): `name: guid(scope.id, principalId, roleId)`, `principalType: 'ServicePrincipal'`, `if (!empty(principalId) && !empty(scopeId))` guards, `roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.X)`.
- ✓ `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0` is NOT referenced anywhere in `infra/` (confirmed). Per Microsoft Learn, this is the correct resource type + apiVersion for Bicep declaration of FICs under existing app regs. Microsoft Graph Bicep extension is loaded automatically when referenced; no `extension microsoftGraph` declaration required.
- ✓ Build artifact convention: `.json` siblings of every `.bicep` are committed (e.g., `acr.bicep` ↔ `acr.json`). Build via `az bicep build` per `infra/CLAUDE.md`.
- ✓ Storage container `scan-artifacts` is defined as a child resource in `blob-storage.bicep:72-76`. Container resourceId format: `{storageAccountId}/blobServices/default/containers/scan-artifacts`. Container-level role-assignment scope is supported (verified via Microsoft Learn).

**External dependency findings (research):**
- ✓ Built-in role GUIDs verified against Microsoft Learn:
  - AcrPull `7f951dda-4ed3-4680-a7ca-43fe172d538d`
  - AcrPush `8311e382-0749-4cb8-b61a-304f252e45ec`
  - Key Vault Secrets User `4633458b-17de-408a-b874-0445c86b69e6`
  - Storage Blob Data Contributor `ba92f5b4-2d11-453d-a403-e96b0029c9fe`
- ✓ FIC resource shape (per Microsoft Graph Bicep v1.0 reference):
  - Type: `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0`
  - Required: `name` (3-120 char URL-friendly, immutable), `issuer`, `subject`, `audiences[]`
  - For GitHub Actions: `issuer = 'https://token.actions.githubusercontent.com'`, `audiences = ['api://AzureADTokenExchange']` exactly
  - Parent referenced via `parent:` symbolic reference to an `existing` `Microsoft.Graph/applications@v1.0` resource (NOT by path string)
  - The existing app reg is referenced by its **uniqueName** (Bicep) — NOT object ID, NOT app/client ID. Microsoft Graph Bicep wraps the GET-by-uniqueName API. Operator captures `uniqueName` when running `az ad app create` (parameter `--display-name` becomes the displayName but the auto-generated uniqueName is in the response).
  - Deploying principal must hold `Application.ReadWrite.OwnedBy` (least priv) or be Owner of the parent app reg.
- ✓ Microsoft Graph Bicep extension is currently published as preview. Microsoft Learn shows it loaded automatically when referenced; no explicit `extension` declaration needed in Bicep file. If `az bicep build` complains, fall back to declaring `provider 'br/public:microsoftGraphV1' as graph` at file top.
- ⚠ Known caveat: the deploying principal (the rac-infra-deploy SP) needs to be added as Owner of each `rac-pipeline-${env}` app reg before bicep can manage child FICs. Documented as a runbook step in Phase 4.

---

## Acceptance Criteria Coverage

This phase produces module source files whose **structure** carries the following AC commitments. Full deployment-time verification of these criteria happens in Phase 5; Phase 1's verification is operational (build succeeds, structure correct).

### pipeline-trust.AC1: Per-env identity + FIC provisioned via bicep
- **pipeline-trust.AC1.3 Success:** FIC `audiences` is exactly `['api://AzureADTokenExchange']` (no extras, no typos).
  - *Phase 1 commitment:* hardcoded literal in `pipeline-identity.bicep`.

### pipeline-trust.AC2: Per-resource least-privilege RBAC
- **pipeline-trust.AC2.4 Failure:** grep over the deployed bicep `.json` artifacts shows zero role assignments at RG, subscription, or platform-KV scope for the pipeline principal.
  - *Phase 1 commitment:* every role-assignment block in `pipeline-identity.bicep` uses `scope:` keyword pointing at a per-resource `existing` reference (ACR / pipeline KV / artifacts container) — never `resourceGroup()` / `subscription()` / platform KV.
- **pipeline-trust.AC2.5 Edge:** when `pipelineAppPrincipalId` is empty, the conditional guards skip role-assignment creation (no empty-principal assignments emitted).
  - *Phase 1 commitment:* every role-assignment block in `pipeline-identity.bicep` carries `if (!empty(pipelineAppPrincipalId) && !empty(<scope-resource-id-param>))`.

**Verifies in tests:** None automated yet (infrastructure phase). `az bicep build` zero-warning success is the verification gate. Structural AC checks are reviewed manually in code-review of the module source.

---

## Notes for the Implementor

- **You are extending `infra/modules/`. Read `infra/CLAUDE.md` before starting.** Key invariants: never hard-code role GUIDs outside `role-ids.bicep`; KV settings are param-driven; tags come from `tags.bicep`; private endpoints are the default for KVs except where there's a specific reason not to (this module is one of those reasons — see below).
- **Pipeline KV intentionally diverges from platform KV's network posture.** Platform KV is private-endpoint-only because everything that reads it (control plane MI, shim MI, App Gateway MI) lives in the VNet. The pipeline KV must be reachable from GitHub Actions runners (external IPs, no VNet peering), so `publicNetworkAccess: 'Enabled'` and `networkAcls.defaultAction: 'Allow'`. RBAC (Key Vault Secrets User) is the sole authentication. Justification documented in design plan §"Callback-secret storage isolation": per-submission HMAC secrets have ~240-min expiry; compromise scope is one submission's already-fired callback secret.
- **Microsoft.Graph FIC resource is the codebase's first use.** The module reference is `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0`. Parent app reg is declared as `existing` by `uniqueName` (string, captured manually after `az ad app create` in Phase 4 runbook). If `az bicep build` errors on the unknown extension, add `provider 'br/public:microsoftGraphV1' as graph` at the top of `pipeline-identity.bicep` and retry.
- **DO NOT add tests in this phase.** This is an infrastructure phase. Verification is "build succeeds, .json artifacts emitted, code review confirms structure". Don't invent unit tests for bicep.
- **DO NOT wire into `main.bicep`** — that's Phase 2. Modules must compile standalone (`az bicep build infra/modules/pipeline-kv.bicep` succeeds with zero warnings) but not be invoked from anywhere yet.
- Activate `ed3d-house-style:coding-effectively` for general project hygiene. There is no Bicep-specific house-style skill; follow the patterns in adjacent existing modules.

---

<!-- START_TASK_1 -->
### Task 1: Extend role-ids.bicep with two new role GUIDs

**Files:**
- Modify: `infra/modules/role-ids.bicep` (append two keys to the `roleIds` map; preserve alphabetical-ish ordering used today, which keeps `keyVault*` keys clustered).

**Implementation:**

Open `/home/sysop/rac/infra/modules/role-ids.bicep`. Add two new keys to the `roleIds` object:

- `acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'` — placed adjacent to existing `acrPull` (line 10).
- `storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'` — placed at end of the map.

The exact GUIDs are the Microsoft-published built-in role IDs (verified against Microsoft Learn `Azure built-in roles` page). Do not invent values.

After the edit the file should be 13 lines (was 11) with the two new keys. The existing `@export()` decorator on `var roleIds` continues to expose every key automatically.

**Verification:**

```bash
cd /home/sysop/rac
az bicep build --file infra/modules/role-ids.bicep
# Expected: no output, exit 0. A `role-ids.json` sibling exists/updates.
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/modules/role-ids.bicep infra/modules/role-ids.json
git commit -m "infra(role-ids): add acrPush + storageBlobDataContributor GUIDs

Required by the new pipeline-identity bicep module which assigns these
roles to the per-env Entra app for the build-and-scan pipeline."
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Create pipeline-kv.bicep — dedicated callback-secrets Key Vault per env

**Files:**
- Create: `infra/modules/pipeline-kv.bicep`

**Implementation:**

Create a new bicep module that provisions one Azure Key Vault dedicated to per-submission HMAC callback secrets, separate from the platform KV. The module also creates one role assignment granting the control-plane managed identity (which mints the secrets) Key Vault Secrets Officer on this vault.

**Module contract (per design plan):**

| | |
|--|--|
| Inputs | `racEnv string`, `location string`, `tags object`, `controlPlaneMiPrincipalId string` |
| Resources | `Microsoft.KeyVault/vaults@2023-07-01` named `kv-rac-pl-${uniqueString(resourceGroup().id)}-${racEnv}`; one `Microsoft.Authorization/roleAssignments@2022-04-01` (Key Vault Secrets Officer for CP MI) scoped to the vault |
| Outputs | `kvId string`, `kvUri string`, `kvName string` |

**Naming:** the vault name must be 3–24 chars and globally unique. Mirror the platform KV's pattern (which uses `uniqueString(subscription().subscriptionId, racEnv)`-derived name) but include the literal string `pipeline` so the two are visually distinguishable: `'kv-rac-pl-${uniqueString(resourceGroup().id, racEnv)}-${racEnv}'`. The `uniqueString()` call is deterministic across redeployments (same inputs ⇒ same hash). If the resulting name exceeds 24 chars, truncate the `uniqueString` arg per existing convention used by the platform KV (verify by looking at `acr.bicep`'s naming pattern).

**KV properties:**
- `enableRbacAuthorization: true` (matches platform KV).
- `enableSoftDelete: true`.
- `softDeleteRetentionInDays: 7` (Azure minimum; design plan §"Additional Considerations" justifies this for dev — staging/prod can override later via param).
- `enablePurgeProtection: null` (NOT `false`; Azure rejects literal-false — use null to omit; see comment in `key-vault.bicep:41-45` for context).
- `publicNetworkAccess: 'Enabled'` — diverges from platform KV. **GH Actions runners are external; they need to reach this vault.**
- `networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }` — accept all source IPs; the only authentication gate is RBAC.
- `accessPolicies: []`.
- `tenantId: subscription().tenantId`.
- SKU `family: 'A', name: 'standard'`.

**No private endpoint.** Do not create a private DNS zone, VNet link, or private endpoint for this vault. (The platform KV has all three; intentional divergence here.)

**Roles:**

Add an `import { roleIds } from './role-ids.bicep'` at the top.

Define a `Microsoft.Authorization/roleAssignments@2022-04-01` resource granting Key Vault Secrets Officer (`b86a8fe4-44ce-4948-aede-518c11a6e2f4` — add this to `role-ids.bicep` as part of this task if not present; it is *not* presently in `role-ids.bicep`).

> **Pre-step inside Task 2:** also extend `role-ids.bicep` with `keyVaultSecretsOfficer: 'b86a8fe4-44ce-4948-aede-518c11a6e2f4'` so the module can reference it. Verify the GUID against [Microsoft Learn — Azure built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles) before committing. (You already extended this file in Task 1; this is a second extension.)

The role assignment shape mirrors `infra/modules/role-assignments.bicep:42-50`:

```bicep
resource cpKvSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneMiPrincipalId)) {
  scope: pipelineKv
  name: guid(pipelineKv.id, controlPlaneMiPrincipalId, roleIds.keyVaultSecretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsOfficer)
    principalId: controlPlaneMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

**Outputs:**
```bicep
@description('Pipeline KV resource ID')
output kvId string = pipelineKv.id

@description('Pipeline KV vault URI (https://...)')
output kvUri string = pipelineKv.properties.vaultUri

@description('Pipeline KV name (kv-rac-pl-...)')
output kvName string = pipelineKv.name
```

**Module header comment** (mirror style of `role-assignments.bicep:1-9`):

```bicep
// ========== MODULE DOCUMENTATION ==========
// pipeline-kv.bicep
// Per-environment dedicated Key Vault holding only per-submission HMAC
// callback secrets used by the build-and-scan pipeline. Intentionally
// separate from the platform KV (kv-rac-...) so the pipeline identity's
// Secrets User RBAC blast radius cannot reach platform secrets like
// rac-pg-admin-password, JWS signing keys, or App Gateway TLS certs.
// publicNetworkAccess is Enabled because GitHub Actions runners (external
// IPs) must reach the vault; RBAC is the sole authn gate.
// =========================================
```

**Verification:**

```bash
cd /home/sysop/rac
az bicep build --file infra/modules/pipeline-kv.bicep
# Expected: no output, exit 0; pipeline-kv.json sibling created.
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/modules/pipeline-kv.bicep infra/modules/pipeline-kv.json infra/modules/role-ids.bicep infra/modules/role-ids.json
git commit -m "infra(pipeline-kv): new module for per-env callback-secrets KV

Creates kv-rac-pl-...-\${racEnv} per environment to hold
per-submission HMAC callback secrets. Separate vault keeps the pipeline
identity's RBAC blast radius isolated from the platform KV. Public
network access is enabled because GitHub Actions runners must reach the
vault from external IPs; RBAC (Secrets User) is the auth gate.

Also adds keyVaultSecretsOfficer role GUID to role-ids.bicep (consumed by
this module to grant the control-plane MI permission to mint secrets)."
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Create pipeline-identity.bicep — FIC + 4 role assignments

**Files:**
- Create: `infra/modules/pipeline-identity.bicep`

**Implementation:**

Create a new bicep module that, given the *manually pre-created* Entra app registration `rac-pipeline-${racEnv}`, provisions:
1. One `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0` whose subject binds the workflow run inside `jdkruzr/rac-pipeline` declaring `environment: ${racEnv}`.
2. Four `Microsoft.Authorization/roleAssignments@2022-04-01` granting AcrPull, AcrPush (on env ACR), Key Vault Secrets User (on the env's pipeline KV from Task 2), Storage Blob Data Contributor (on the env's `scan-artifacts` blob container only).

**Module contract (per design plan):**

| | |
|--|--|
| Inputs | `racEnv string`, `pipelineAppUniqueName string`, `pipelineAppPrincipalId string`, `pipelineGithubOwner string`, `pipelineGithubRepo string`, `acrId string`, `pipelineKvId string`, `artifactsBlobContainerId string` |
| Resources | one FIC + four roleAssignments |
| Outputs | `ficResourceId string`, `roleAssignmentIds array` |

**Inputs deviation from design plan:** the design's contract sketch lists `pipelineAppObjectId string`. After research, the Microsoft Graph Bicep extension references the parent app reg by `uniqueName`, not object ID. Rename the input to `pipelineAppUniqueName` and update the design plan's contract reference is NOT in scope (we're implementing, not amending design); just use the correct name.

**Imports:**
```bicep
import { roleIds } from './role-ids.bicep'
```

**Existing-resource references** (Bicep `existing` keyword for resources created elsewhere — these are read-only handles for `scope:`):

```bicep
resource pipelineApp 'Microsoft.Graph/applications@v1.0' existing = {
  uniqueName: pipelineAppUniqueName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(split(acrId, '/'))
}

resource pipelineKv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: last(split(pipelineKvId, '/'))
}

// Container resourceId format: {storageAcctId}/blobServices/default/containers/{name}
// Split by '/' and pick last 5 segments to reconstruct hierarchy for `existing`.
resource artifactsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  // Use the parent-by-symbolic-reference pattern; alternative is the
  // 'storageAcct/default/containerName' name-string but that is fragile.
  // Construct the parent chain from the resourceId.
  name: '${split(artifactsBlobContainerId, '/')[8]}/default/${last(split(artifactsBlobContainerId, '/'))}'
}
```

**FIC resource:**

```bicep
resource pipelineFic 'Microsoft.Graph/applications/federatedIdentityCredentials@v1.0' = {
  parent: pipelineApp
  name: 'rac-pipeline-${racEnv}-gha'
  audiences: ['api://AzureADTokenExchange']
  issuer: 'https://token.actions.githubusercontent.com'
  subject: 'repo:${pipelineGithubOwner}/${pipelineGithubRepo}:environment:${racEnv}'
  description: 'GitHub Actions OIDC for ${pipelineGithubOwner}/${pipelineGithubRepo} on environment ${racEnv}'
}
```

**Four role assignments** (each wrapped in `if (!empty(pipelineAppPrincipalId) && !empty(<scope-id-param>))`):

```bicep
resource raAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(pipelineAppPrincipalId) && !empty(acrId)) {
  scope: acr
  name: guid(acr.id, pipelineAppPrincipalId, roleIds.acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPull)
    principalId: pipelineAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(pipelineAppPrincipalId) && !empty(acrId)) {
  scope: acr
  name: guid(acr.id, pipelineAppPrincipalId, roleIds.acrPush)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPush)
    principalId: pipelineAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(pipelineAppPrincipalId) && !empty(pipelineKvId)) {
  scope: pipelineKv
  name: guid(pipelineKv.id, pipelineAppPrincipalId, roleIds.keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
    principalId: pipelineAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource raBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(pipelineAppPrincipalId) && !empty(artifactsBlobContainerId)) {
  scope: artifactsContainer
  name: guid(artifactsContainer.id, pipelineAppPrincipalId, roleIds.storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageBlobDataContributor)
    principalId: pipelineAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}
```

**Outputs:**
```bicep
@description('FIC resource ID (empty when pipelineAppUniqueName is empty)')
output ficResourceId string = pipelineFic.id

@description('Resource IDs of the four role assignments. Empty strings for those skipped by guards.')
output roleAssignmentIds array = [
  !empty(pipelineAppPrincipalId) && !empty(acrId) ? raAcrPull.id : ''
  !empty(pipelineAppPrincipalId) && !empty(acrId) ? raAcrPush.id : ''
  !empty(pipelineAppPrincipalId) && !empty(pipelineKvId) ? raKvSecretsUser.id : ''
  !empty(pipelineAppPrincipalId) && !empty(artifactsBlobContainerId) ? raBlobContributor.id : ''
]
```

**Module header comment:**
```bicep
// ========== MODULE DOCUMENTATION ==========
// pipeline-identity.bicep
// Codifies trust + RBAC for the per-env pipeline Entra app reg.
// Prerequisites (manual; documented in docs/runbooks/bootstrap.md):
//   - rac-pipeline-${racEnv} app reg exists in the tenant
//   - Deploy SP is Owner of that app reg (so this module can manage child
//     federatedIdentityCredentials)
//   - Caller has captured pipelineAppUniqueName + pipelineAppPrincipalId
//     and threaded them via bicepparam
// Resources created (gated on !empty(pipelineAppPrincipalId)):
//   - One Microsoft.Graph FIC trusting the GH Actions OIDC issuer with
//     subject 'repo:${owner}/${repo}:environment:${racEnv}'
//   - Four Microsoft.Authorization roleAssignments at per-RESOURCE scope
//     (NEVER RG / subscription / platform KV):
//       AcrPull, AcrPush       on env ACR
//       KV Secrets User        on env pipeline KV (NOT platform KV)
//       Storage Blob Data Contributor on env artifacts container only
// =========================================
```

**Caveat for the implementor:** `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0` is currently published as a Microsoft Graph Bicep preview extension. If `az bicep build` errors with an unknown-resource-type message, add this as the first non-blank line of the file:

```bicep
provider 'br/public:microsoftgraphv1.0' as graph
```

…then retry. Either form should work; the explicit `provider` decl is the documented escape hatch when auto-loading misses.

**Verification:**

```bash
cd /home/sysop/rac
az bicep build --file infra/modules/pipeline-identity.bicep
# Expected: no output, exit 0; pipeline-identity.json sibling created.
```

If it warns about `Microsoft.Graph/applications/federatedIdentityCredentials@v1.0` being preview/experimental, that is acceptable; it must not error.

**Manual structural review (verifies AC1.3, AC2.4, AC2.5):**

The design's AC2.4 explicitly says "grep over the deployed bicep `.json` artifacts" — so the verification grep targets `pipeline-identity.json` (the rendered ARM template), not the `.bicep` source. The `.bicep`-source grep is a useful structural check too; both run.

```bash
cd /home/sysop/rac

# AC1.3: audiences hardcoded to exact value
grep -n "api://AzureADTokenExchange" infra/modules/pipeline-identity.bicep
# Expected: exactly one line containing the literal array ['api://AzureADTokenExchange']

# AC1.3 (rendered template too)
grep -o "api://AzureADTokenExchange" infra/modules/pipeline-identity.json | wc -l
# Expected: at least 1 (the audiences array element rendered into the JSON).

# AC2.4 (PRIMARY — design says "deployed .json artifacts"):
#   zero scopes pointing to RG / subscription / platform-KV in the rendered ARM template
grep -E '"scope":\s*"\[resourceGroup\(\)\.id\]"|"scope":\s*"\[subscription\(\)\.id\]"' infra/modules/pipeline-identity.json
# Expected: no matches.
grep -E '"scope":\s*"\[resourceId\(.Microsoft\.KeyVault/vaults., .kv-rac-[^p]' infra/modules/pipeline-identity.json
# Expected: no matches (no scope to a KV whose name starts kv-rac- but NOT kv-rac-pl-).

# AC2.4 (SECONDARY structural check on .bicep source for clarity):
grep -nE "scope:\s+(resourceGroup\(\)|subscription\(\))" infra/modules/pipeline-identity.bicep
# Expected: zero hits.
grep -nE "scope:.*kv-rac-[^p]" infra/modules/pipeline-identity.bicep
# Expected: zero hits.

# AC2.5: every role assignment guarded on !empty(pipelineAppPrincipalId) in the source
grep -c "if (!empty(pipelineAppPrincipalId)" infra/modules/pipeline-identity.bicep
# Expected: 4 (one per role assignment).
```

**Commit:**

```bash
cd /home/sysop/rac
git add infra/modules/pipeline-identity.bicep infra/modules/pipeline-identity.json
git commit -m "infra(pipeline-identity): new module for per-env FIC + 4 RBAC

Codifies the trust shape for the per-env rac-pipeline-\${racEnv} Entra
app reg: one Microsoft.Graph federatedIdentityCredentials@v1.0 trusting
GitHub Actions OIDC tokens whose subject matches
'repo:\${owner}/\${repo}:environment:\${racEnv}', plus four
resource-scoped roleAssignments (AcrPull, AcrPush, KV Secrets User on
the env pipeline KV, Storage Blob Data Contributor on the env artifacts
container only). All assignments guarded on
!empty(pipelineAppPrincipalId) so the module is safe to compile with
empty principal IDs (Pass-1 deploy gate)."
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Verify all module .json artifacts are present and committed

**Files:** none (verification + tidy-up).

**Implementation:**

After Tasks 1-3, the following `.bicep` ↔ `.json` pairs must exist:
- `infra/modules/role-ids.bicep` ↔ `infra/modules/role-ids.json` (modified)
- `infra/modules/pipeline-kv.bicep` ↔ `infra/modules/pipeline-kv.json` (new)
- `infra/modules/pipeline-identity.bicep` ↔ `infra/modules/pipeline-identity.json` (new)

**Verification:**

```bash
cd /home/sysop/rac

# All three modules build clean
az bicep build --file infra/modules/role-ids.bicep
az bicep build --file infra/modules/pipeline-kv.bicep
az bicep build --file infra/modules/pipeline-identity.bicep

# All three .json siblings exist
ls -1 infra/modules/{role-ids,pipeline-kv,pipeline-identity}.json
# Expected: three lines, no errors.

# Working tree clean (everything committed in Tasks 1-3)
git status --short infra/modules/
# Expected: empty output.
```

If `git status` shows uncommitted changes (likely just regenerated `.json` from a re-run of `az bicep build`), commit them:

```bash
git add infra/modules/role-ids.json infra/modules/pipeline-kv.json infra/modules/pipeline-identity.json
git commit -m "infra(modules): regenerate .json artifacts from bicep sources"
```

**Commit:** None unless re-build introduced diffs (covered above).
<!-- END_TASK_4 -->

---

## Phase 1 Done When

- All four task verifications pass.
- Three new/modified bicep modules build clean (zero warnings, zero errors) via `az bicep build`.
- Three `.json` siblings are present and committed.
- Manual structural greps confirm: AC1.3 audiences exact match, AC2.4 zero RG/sub/platform-KV scopes, AC2.5 four `!empty(pipelineAppPrincipalId)` guards.
- `main.bicep` is **untouched** — Phase 2 will integrate the modules.
- The codebase still passes whatever lint / typecheck the project runs on bicep (currently just `az bicep build`).
