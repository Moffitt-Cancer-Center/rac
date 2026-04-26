@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('Azure Container Registry name (globally unique, max 50 chars, alphanumeric)')
param acrName string

@description('Private endpoint subnet resource ID')
param peSubnetId string

@description('VNet resource ID for private DNS zone linking')
param vnetId string

@description('Resource tags applied to all resources')
param tags object

var acrNameNormalized = toLower(acrName)

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrNameNormalized
  location: location
  tags: tags
  sku: {
    name: 'Premium'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Disabled'
    policies: {
      // Quarantine policy holds pushed images until an external scanner
      // explicitly releases them. We don't run a quarantine-releasing
      // scanner against this registry — researcher image scanning happens
      // downstream in the rac-pipeline (Defender for Containers + Grype),
      // and Tier-2 platform images (control plane, shim) are operator-
      // controlled. Leaving this enabled with no releaser causes every
      // pushed tag to disappear ("MANIFEST_UNKNOWN") on pull.
      quarantinePolicy: {
        status: 'disabled'
      }
    }
  }
}

// Order matters: declare DNS zone + VNet link BEFORE the private endpoint and
// the privateDnsZoneGroup. ARM schedules independent resources in parallel
// batches; if the privateDnsZoneGroup runs in the same batch as the zone, the
// zone's resource ID can resolve to empty when the group consumes it,
// producing "invalid private dns zone ids ." at deploy time. Matching the
// layout of key-vault.bicep / blob-storage.bicep / postgres.bicep.

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  // TODO: For sovereign/gov clouds, this should be dynamically determined
  name: 'privatelink.azurecr.io'
  location: 'global'
  tags: tags
}

resource privateDnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: 'vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
  tags: tags
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-acr-${racEnv}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'acr-connection'
        properties: {
          privateLinkServiceId: acr.id
          groupIds: [
            'registry'
          ]
        }
      }
    ]
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: privateEndpoint
  name: 'acr-zone-group'
  // Belt-and-suspenders: explicit dependsOn so ARM cannot schedule the group
  // before the zone exists, even though Bicep already infers the dependency
  // via privateDnsZone.id.
  dependsOn: [privateDnsZone]
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'acr-privatelink-zone'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

@description('Container Registry resource ID')
output acrId string = acr.id

@description('Container Registry login server FQDN')
output acrLoginServer string = acr.properties.loginServer

@description('Container Registry resource ID (alias for acrId)')
output acrResourceId string = acr.id
