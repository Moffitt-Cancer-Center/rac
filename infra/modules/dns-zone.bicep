import { roleIds } from './role-ids.bicep'

@description('Parent DNS domain, e.g., rac.moffitt.org')
param parentDomain string

@description('Control Plane managed identity principal ID (empty on first deploy; populated in Phase 5 Task 1 re-deploy)')
param controlPlaneIdentityPrincipalId string = ''

@description('Resource tags')
param tags object

// Create the public DNS zone
resource dnsZone 'Microsoft.Network/dnsZones@2018-05-01' = {
  name: parentDomain
  location: 'global'
  tags: tags
}

// Conditional role assignment: grant Control Plane MI DNS Zone Contributor role
// Only created when controlPlaneIdentityPrincipalId is non-empty (Phase 5 re-deploy)
resource dnsZoneRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(controlPlaneIdentityPrincipalId)) {
  name: guid(dnsZone.id, controlPlaneIdentityPrincipalId, roleIds.dnsZoneContributor)
  scope: dnsZone
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.dnsZoneContributor)
    principalId: controlPlaneIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

@description('DNS Zone resource ID')
output zoneId string = dnsZone.id

@description('DNS Zone name servers')
output zoneNameServers array = dnsZone.properties.nameServers
