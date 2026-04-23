@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('Resource tags applied to all resources')
param tags object

// Control Plane managed identity
resource controlPlaneMi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: 'id-rac-controlplane-${racEnv}'
  location: location
  tags: tags
}

// Shim managed identity
resource shimMi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: 'id-rac-shim-${racEnv}'
  location: location
  tags: tags
}

@description('Control Plane managed identity resource ID')
output controlPlaneMiResourceId string = controlPlaneMi.id

@description('Control Plane managed identity principal ID')
output controlPlaneMiPrincipalId string = controlPlaneMi.properties.principalId

@description('Control Plane managed identity client ID')
output controlPlaneMiClientId string = controlPlaneMi.properties.clientId

@description('Shim managed identity resource ID')
output shimMiResourceId string = shimMi.id

@description('Shim managed identity principal ID')
output shimMiPrincipalId string = shimMi.properties.principalId

@description('Shim managed identity client ID')
output shimMiClientId string = shimMi.properties.clientId
