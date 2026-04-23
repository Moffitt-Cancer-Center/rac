@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('Key Vault name (must be globally unique, 3-24 alphanumeric)')
param kvName string

@description('Entra tenant ID for the subscription')
param tenantId string

@description('Private Endpoint subnet resource ID')
param peSubnetId string

@description('Resource tags applied to all resources')
param tags object

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
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
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

@description('Key Vault resource ID')
output kvId string = keyVault.id

@description('Key Vault URI')
output kvUri string = keyVault.properties.vaultUri

@description('Key Vault name')
output kvName string = keyVault.name
