@description('Azure region for all resources')
param location string

@description('Postgres Flexible Server name')
param serverName string

@description('Postgres administrator login password (must come from Key Vault reference)')
@secure()
param adminPassword string

@description('Postgres SKU name (e.g., Standard_B2s, Standard_D2s_v3)')
param skuName string

@description('Postgres SKU tier (e.g., Burstable, GeneralPurpose)')
param skuTier string

@description('Storage size in GB')
param storageSizeGB int

@description('High Availability mode (Disabled, SameZone, ZoneRedundant)')
param haMode string

@description('Backup retention days')
param backupRetentionDays int

@description('Postgres private endpoint subnet resource ID')
param peSubnetId string

@description('VNet resource ID for private DNS zone linking')
param vnetId string

@description('Postgres extensions to enable')
param extensions array = ['pg_uuidv7']

@description('Geo-redundant backup setting (Enabled or Disabled)')
@allowed(['Enabled', 'Disabled'])
param geoRedundantBackup string = 'Disabled'

@description('Resource tags applied to all resources')
param tags object

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: '16'
    administratorLogin: 'rac_admin'
    administratorLoginPassword: adminPassword
    storage: {
      storageSizeGB: storageSizeGB
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: geoRedundantBackup
    }
    highAvailability: {
      mode: haMode
    }
  }
}

// Configuration: enable extensions in azure.extensions
resource postgresConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview' = {
  parent: postgresServer
  name: 'azure.extensions'
  properties: {
    value: join(extensions, ',')
    source: 'user-override'
  }
}

// Private DNS zone for postgres
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

// VNet link for private DNS zone
resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: 'link-postgres-${location}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// Private Endpoint for Postgres
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-postgres-${location}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'postgres-connection'
        properties: {
          privateLinkServiceId: postgresServer.id
          groupIds: [
            'postgresqlServer'
          ]
        }
      }
    ]
  }
}

// Private DNS Zone Group for Private Endpoint
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'postgres-zone-group'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'postgres-config'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

@description('Postgres server resource ID')
output serverId string = postgresServer.id

@description('Postgres server FQDN')
output serverFqdn string = postgresServer.properties.fullyQualifiedDomainName
