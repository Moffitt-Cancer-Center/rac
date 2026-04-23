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
      quarantinePolicy: {
        status: 'enabled'
      }
    }
  }
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

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  // TODO: For sovereign/gov clouds, this should be dynamically determined
  name: 'privatelink.azurecr.io'
  location: 'global'
  tags: tags
}

// VNet link for private DNS zone
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

@description('Container Registry resource ID')
output acrId string = acr.id

@description('Container Registry login server FQDN')
output acrLoginServer string = acr.properties.loginServer

@description('Container Registry resource ID (alias for acrId)')
output acrResourceId string = acr.id
