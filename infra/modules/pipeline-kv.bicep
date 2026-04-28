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

import { roleIds } from './role-ids.bicep'

@description('Environment name (dev, staging, prod)')
param racEnv string

@description('Azure region for all resources')
param location string

@description('Resource tags applied to all resources')
param tags object

@description('Control Plane managed identity principal ID for Key Vault Secrets Officer grant')
param controlPlaneMiPrincipalId string = ''

resource pipelineKv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-rac-pl-${take(uniqueString(resourceGroup().id, racEnv), 6)}-${racEnv}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    accessPolicies: []
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Azure rejects explicit `enablePurgeProtection: false` — the property
    // can only be omitted or set to true (irreversible once on). Setting to
    // null here causes the Bicep compiler to omit the property from the ARM
    // body, which Azure accepts as "not enabled".
    enablePurgeProtection: null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Control Plane MI gets Key Vault Secrets Officer so it can mint per-submission
// HMAC callback secrets into this vault.
resource cpKvSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneMiPrincipalId)) {
  scope: pipelineKv
  name: guid(pipelineKv.id, controlPlaneMiPrincipalId, roleIds.keyVaultSecretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsOfficer)
    principalId: controlPlaneMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

@description('Pipeline KV resource ID')
output kvId string = pipelineKv.id

@description('Pipeline KV vault URI (https://...)')
output kvUri string = pipelineKv.properties.vaultUri

@description('Pipeline KV name (kv-rac-pipeline-...)')
output kvName string = pipelineKv.name
