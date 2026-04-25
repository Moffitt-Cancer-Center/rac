// Cross-RG role assignments on the bootstrap Key Vault.
//
// The bootstrap KV (rg-rac-bootstrap/kv-rac-bootstrap-001) holds the TLS
// certificate that App Gateway reads at provisioning time. The App Gateway
// MI must have Secrets User + Certificates User on this vault BEFORE the
// gateway is created — otherwise App Gateway fails with "Cannot read secret
// from Key Vault" at create-time.
//
// This module is scoped to rg-rac-bootstrap (cross-RG from main.bicep's
// rg-rac-<env> scope). The bootstrap RG is created by
// scripts/demo-bootstrap/bootstrap-kv.sh as a one-time setup step shared
// across env rebuilds.

targetScope = 'resourceGroup'

import { roleIds } from './role-ids.bicep'

@description('Bootstrap Key Vault name (defaults to kv-rac-bootstrap-001 — the demo-bootstrap convention)')
param bootstrapKvName string = 'kv-rac-bootstrap-001'

@description('App Gateway managed identity principal ID')
param appGwMiPrincipalId string

resource bootstrapKv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: bootstrapKvName
}

resource secretsUserForAppGw 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: bootstrapKv
  name: guid(bootstrapKv.id, appGwMiPrincipalId, roleIds.keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
    principalId: appGwMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource certificatesUserForAppGw 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: bootstrapKv
  name: guid(bootstrapKv.id, appGwMiPrincipalId, roleIds.keyVaultCertificatesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultCertificatesUser)
    principalId: appGwMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}
