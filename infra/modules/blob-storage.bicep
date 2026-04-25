@description('Azure region for all resources')
param location string

@description('Storage account name (must be globally unique, 3-24 lowercase alphanumeric)')
param storageAccountName string

@description('Private Endpoint subnet resource ID')
param peSubnetId string

@description('VNet resource ID for private DNS zone linking')
param vnetId string

@description('Storage SKU (Standard_GRS for prod, Standard_LRS for dev)')
param sku string = 'Standard_GRS'

@description('Resource tags applied to all resources')
param tags object

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: sku
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
    }
  }
}

// Bicep auto-derives `dependsOn: [storageAccount]` for child resources via
// `parent:`, but we add explicit dependsOn anywhere children share a parent
// to serialize ARM's batched parallel scheduling. The observed
// `ResourceNotFound` failures on first deploy come from a race where ARM
// reads the storage account from a sibling control-plane endpoint before
// it's fully populated.

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  dependsOn: [storageAccount]
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

// Containers: researcher-uploads, scan-artifacts, sboms, cost-exports, build-logs.
// Containers share a parent (blobServices). ARM treats sibling-with-same-parent
// resources as parallelizable — but Storage's container API serializes them
// internally anyway, and explicit dependsOn chains avoid intermittent 409 races.
resource containerResearcherUploads 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'researcher-uploads'
  dependsOn: [blobServices]
}

resource containerScanArtifacts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'scan-artifacts'
  dependsOn: [containerResearcherUploads]
}

resource containerSboms 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'sboms'
  dependsOn: [containerScanArtifacts]
}

resource containerCostExports 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'cost-exports'
  dependsOn: [containerSboms]
}

resource containerBuildLogs 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: 'build-logs'
  dependsOn: [containerCostExports]
}

// Management policy depends on all containers existing — Storage rejects
// lifecycle rules that filter on prefixes for which no container exists.
resource managementPolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  dependsOn: [containerBuildLogs]
  properties: {
    policy: {
      rules: [
        {
          enabled: true
          name: 'ScanArtifactsLifecycle'
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                'scan-artifacts/'
              ]
            }
            actions: {
              baseBlob: {
                tierToCool: {
                  daysAfterModificationGreaterThan: 60
                }
                tierToArchive: {
                  daysAfterModificationGreaterThan: 365
                }
              }
            }
          }
        }
        {
          enabled: true
          name: 'SbomsLifecycle'
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                'sboms/'
              ]
            }
            actions: {
              baseBlob: {
                tierToCool: {
                  daysAfterModificationGreaterThan: 60
                }
                tierToArchive: {
                  daysAfterModificationGreaterThan: 365
                }
              }
            }
          }
        }
        {
          enabled: true
          name: 'BuildLogsLifecycle'
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                'build-logs/'
              ]
            }
            actions: {
              baseBlob: {
                tierToCool: {
                  daysAfterModificationGreaterThan: 60
                }
                tierToArchive: {
                  daysAfterModificationGreaterThan: 365
                }
              }
            }
          }
        }
        {
          enabled: true
          name: 'CostExportsDelete'
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                'cost-exports/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: 730
                }
              }
            }
          }
        }
      ]
    }
  }
}

// Private DNS zone for Blob Storage
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

// VNet link for private DNS zone
resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: 'link-blob-${location}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// Private endpoint for the blob subresource. Wait for managementPolicy so
// the entire data-plane setup is done before locking down network access.
resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${storageAccountName}-blob'
  location: location
  tags: tags
  dependsOn: [managementPolicy]
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'pec-${storageAccountName}-blob'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

// Private DNS Zone Group for Private Endpoint. Explicit dependsOn so ARM
// can't schedule the group before the zone (mirrors the A1 fix for ACR).
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: blobPrivateEndpoint
  name: 'blob-zone-group'
  dependsOn: [privateDnsZone]
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob-config'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

@description('Storage account resource ID')
output storageAccountId string = storageAccount.id

@description('Storage account name')
output storageAccountName string = storageAccount.name

@description('Blob endpoint')
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
