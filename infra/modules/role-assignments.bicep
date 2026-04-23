@description('Control Plane managed identity principal ID (empty on first deploy; populated in Phase 5 Task 1 re-deploy)')
param controlPlaneMiPrincipalId string = ''

@description('Shim managed identity principal ID (empty on first deploy; populated in Phase 6)')
param shimMiPrincipalId string = ''

@description('Key Vault resource ID (platform Key Vault, for KV assignments only)')
param kvResourceId string = ''

// ========== KEY VAULT ROLE ASSIGNMENTS ==========
// Scoped to platform RG where Key Vault is deployed
// Grants crypto permissions to managed identities

// Reference the existing Key Vault using its resource ID
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (!empty(kvResourceId)) {
  name: last(split(kvResourceId, '/'))
}

// Key Vault Crypto Officer role for Control Plane MI
// Role ID: 14b46e9e-c2b7-41b4-b07b-48a6ebf60603
// Allows the Control Plane to encrypt/decrypt secrets for Tier 3 provisioning
resource kvCryptoOfficerForControlPlane 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneMiPrincipalId) && !empty(kvResourceId)) {
  scope: kv
  name: guid(kv.id, controlPlaneMiPrincipalId, '14b46e9e-c2b7-41b4-b07b-48a6ebf60603')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '14b46e9e-c2b7-41b4-b07b-48a6ebf60603')
    principalId: controlPlaneMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Crypto User role for Shim MI
// Role ID: 12338af0-0e69-4776-bea7-57ae8d297424
// Allows the Shim to read public keys for token validation (read-only crypto operations)
resource kvCryptoUserForShim 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(shimMiPrincipalId) && !empty(kvResourceId)) {
  scope: kv
  name: guid(kv.id, shimMiPrincipalId, '12338af0-0e69-4776-bea7-57ae8d297424')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '12338af0-0e69-4776-bea7-57ae8d297424')
    principalId: shimMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ========== TIER 3 RG ROLE ASSIGNMENTS ==========
// Scoped to Tier 3 RG; this assignment is conditional on Phase 5 re-deploy

// Contributor role for Control Plane MI on the current resource group (when scoped to Tier 3 RG)
// Role ID: b24988ac-6180-42a0-ab88-20f7382dd24c
// Allows Control Plane to create and manage ACA apps and supporting resources in Tier 3
resource tier3ContributorForControlPlane 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneMiPrincipalId)) {
  name: guid(resourceGroup().id, controlPlaneMiPrincipalId, 'b24988ac-6180-42a0-ab88-20f7382dd24c')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
    principalId: controlPlaneMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ========== OUTPUTS ==========

@description('Key Vault Crypto Officer role assignment resource ID (if created)')
output kvCryptoOfficerRoleAssignmentId string = !empty(controlPlaneMiPrincipalId) && !empty(kvResourceId) ? kvCryptoOfficerForControlPlane.id : ''

@description('Key Vault Crypto User role assignment resource ID (if created)')
output kvCryptoUserRoleAssignmentId string = !empty(shimMiPrincipalId) && !empty(kvResourceId) ? kvCryptoUserForShim.id : ''

@description('Tier 3 Contributor role assignment resource ID (if created)')
output tier3ContributorRoleAssignmentId string = !empty(controlPlaneMiPrincipalId) ? tier3ContributorForControlPlane.id : ''
