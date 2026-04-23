import { roleIds } from './role-ids.bicep'

// ========== MODULE DOCUMENTATION ==========
// This module is designed to be invoked MULTIPLE TIMES from main.bicep with different scopes.
// Each invocation's `if` guards on non-empty params select which role assignments run:
//   Invocation 1: scope=platform RG, KV role assignments (kvResourceId non-empty, tier3 params empty).
//   Invocation 2: scope=Tier 3 RG, Contributor assignment (tier3 params non-empty, kvResourceId empty).
// =========================================

@description('Control Plane managed identity principal ID (empty on first deploy; populated in Phase 5 Task 1 re-deploy)')
param controlPlaneMiPrincipalId string = ''

@description('Shim managed identity principal ID (empty on first deploy; populated in Phase 6)')
param shimMiPrincipalId string = ''

@description('App Gateway managed identity principal ID (empty until MI is created)')
param appGwMiPrincipalId string = ''

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
// Allows the Control Plane to encrypt/decrypt secrets for Tier 3 provisioning
resource kvCryptoOfficerForControlPlane 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneMiPrincipalId) && !empty(kvResourceId)) {
  scope: kv
  name: guid(kv.id, controlPlaneMiPrincipalId, roleIds.keyVaultCryptoOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultCryptoOfficer)
    principalId: controlPlaneMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Crypto User role for Shim MI
// Allows the Shim to read public keys for token validation (read-only crypto operations)
resource kvCryptoUserForShim 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(shimMiPrincipalId) && !empty(kvResourceId)) {
  scope: kv
  name: guid(kv.id, shimMiPrincipalId, roleIds.keyVaultCryptoUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultCryptoUser)
    principalId: shimMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Secrets User role for App Gateway MI
// Allows App Gateway to read TLS certificate from Key Vault
resource kvSecretsUserForAppGw 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(appGwMiPrincipalId) && !empty(kvResourceId)) {
  scope: kv
  name: guid(kv.id, appGwMiPrincipalId, roleIds.keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
    principalId: appGwMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Certificates User role for App Gateway MI
// Allows App Gateway to read TLS certificate details from Key Vault
resource kvCertificatesUserForAppGw 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(appGwMiPrincipalId) && !empty(kvResourceId)) {
  scope: kv
  name: guid(kv.id, appGwMiPrincipalId, roleIds.keyVaultCertificatesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultCertificatesUser)
    principalId: appGwMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ========== TIER 3 RG ROLE ASSIGNMENTS ==========
// Scoped to Tier 3 RG; this assignment is conditional on Phase 5 re-deploy

// Contributor role for Control Plane MI on the current resource group (when scoped to Tier 3 RG)
// Allows Control Plane to create and manage ACA apps and supporting resources in Tier 3
resource tier3ContributorForControlPlane 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneMiPrincipalId)) {
  name: guid(resourceGroup().id, controlPlaneMiPrincipalId, roleIds.contributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.contributor)
    principalId: controlPlaneMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ========== OUTPUTS ==========

@description('Key Vault Crypto Officer role assignment resource ID (if created)')
output kvCryptoOfficerRoleAssignmentId string = !empty(controlPlaneMiPrincipalId) && !empty(kvResourceId) ? kvCryptoOfficerForControlPlane.id : ''

@description('Key Vault Crypto User role assignment resource ID (if created)')
output kvCryptoUserRoleAssignmentId string = !empty(shimMiPrincipalId) && !empty(kvResourceId) ? kvCryptoUserForShim.id : ''

@description('Key Vault Secrets User role assignment for App Gateway (if created)')
output kvSecretsUserRoleAssignmentId string = !empty(appGwMiPrincipalId) && !empty(kvResourceId) ? kvSecretsUserForAppGw.id : ''

@description('Key Vault Certificates User role assignment for App Gateway (if created)')
output kvCertificatesUserRoleAssignmentId string = !empty(appGwMiPrincipalId) && !empty(kvResourceId) ? kvCertificatesUserForAppGw.id : ''

@description('Tier 3 Contributor role assignment resource ID (if created)')
output tier3ContributorRoleAssignmentId string = !empty(controlPlaneMiPrincipalId) ? tier3ContributorForControlPlane.id : ''
