@description('Azure region for all resources')
param location string

@description('Key Vault name (must be globally unique, 3-24 alphanumeric)')
param kvName string

@description('Entra tenant ID for the subscription')
param tenantId string

@description('Private Endpoint subnet resource ID')
param peSubnetId string

@description('VNet resource ID for private DNS zone linking')
param vnetId string

@description('Resource tags applied to all resources')
param tags object

@description('Purge protection — set false only for dev to allow cleanup')
param enablePurgeProtection bool = true

@description('Soft-delete retention in days (7-90)')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 90

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    accessPolicies: []
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    enablePurgeProtection: enablePurgeProtection
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

// Private DNS zone for Key Vault
// TODO: For sovereign/gov clouds, this should be dynamically determined via environment().suffixes
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

// VNet link for private DNS zone
resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: 'link-kv-${location}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// Private endpoint for the vault subresource
resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${kvName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'pec-${kvName}'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

// Private DNS Zone Group for Private Endpoint
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: keyVaultPrivateEndpoint
  name: 'kv-zone-group'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'kv-config'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

@description('Key Vault resource ID')
output kvId string = keyVault.id

@description('Key Vault URI')
output kvUri string = keyVault.properties.vaultUri

@description('Key Vault name')
output keyVaultName string = keyVault.name
