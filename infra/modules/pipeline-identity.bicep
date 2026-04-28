extension 'br:mcr.microsoft.com/bicep/extensions/microsoftgraph/v1.0:0.1.8-preview'

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

import { roleIds } from './role-ids.bicep'

@description('Environment name (dev, staging, prod)')
param racEnv string

@description('Pipeline app registration unique name (from az ad app create)')
param pipelineAppUniqueName string = ''

@description('Pipeline app registration principal ID (object ID, empty on Pass-1 deploy)')
param pipelineAppPrincipalId string = ''

@description('GitHub repo owner (for FIC subject)')
param pipelineGithubOwner string

@description('GitHub repo name (for FIC subject)')
param pipelineGithubRepo string

@description('ACR resource ID')
param acrId string = ''

@description('Pipeline Key Vault resource ID (from pipeline-kv.bicep output)')
param pipelineKvId string = ''

@description('Artifacts blob container resource ID (format: {storageAcctId}/blobServices/default/containers/scan-artifacts)')
param artifactsBlobContainerId string = ''

// ========== EXISTING RESOURCE REFERENCES ==========

// Note: the parent app reg (Microsoft.Graph/applications) is referenced
// implicitly via the composite name '${pipelineAppUniqueName}/<fic-name>'.
// We do not declare an `existing` symbolic reference because it would be
// unused (Bicep linter flags it) and a guarded existing resource confuses
// the null-flow analyzer (BCP318) when other resources reference it.

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (!empty(acrId)) {
  name: last(split(acrId, '/'))
}

resource pipelineKv 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (!empty(pipelineKvId)) {
  name: last(split(pipelineKvId, '/'))
}

// Container resourceId format: {storageAcctId}/blobServices/default/containers/{name}
// Split by '/' and pick last 5 segments to reconstruct hierarchy for `existing`.
resource artifactsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = if (!empty(artifactsBlobContainerId)) {
  // Use the parent-by-symbolic-reference pattern; alternative is the
  // 'storageAcct/default/containerName' name-string but that is fragile.
  // Construct the parent chain from the resourceId.
  name: '${split(artifactsBlobContainerId, '/')[8]}/default/${last(split(artifactsBlobContainerId, '/'))}'
}

// ========== FEDERATED IDENTITY CREDENTIAL ==========

resource pipelineFic 'Microsoft.Graph/applications/federatedIdentityCredentials@v1.0' = if (!empty(pipelineAppUniqueName)) {
  name: '${pipelineAppUniqueName}/rac-pipeline-${racEnv}-gha'
  audiences: ['api://AzureADTokenExchange']
  issuer: 'https://token.actions.githubusercontent.com'
  subject: 'repo:${pipelineGithubOwner}/${pipelineGithubRepo}:environment:${racEnv}'
  description: 'GitHub Actions OIDC for ${pipelineGithubOwner}/${pipelineGithubRepo} on environment ${racEnv}'
}

// ========== ROLE ASSIGNMENTS ==========

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

// ========== OUTPUTS ==========

@description('FIC resource ID (empty when pipelineAppUniqueName is empty)')
output ficResourceId string = pipelineFic.?id ?? ''

@description('Resource IDs of the four role assignments. Empty strings for those skipped by guards.')
output roleAssignmentIds array = [
  !empty(pipelineAppPrincipalId) && !empty(acrId) ? raAcrPull.id : ''
  !empty(pipelineAppPrincipalId) && !empty(acrId) ? raAcrPush.id : ''
  !empty(pipelineAppPrincipalId) && !empty(pipelineKvId) ? raKvSecretsUser.id : ''
  !empty(pipelineAppPrincipalId) && !empty(artifactsBlobContainerId) ? raBlobContributor.id : ''
]
