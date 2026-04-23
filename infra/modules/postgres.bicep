@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

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
param pgSubnetId string

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
      geoRedundantBackup: 'Enabled'
    }
    highAvailability: {
      mode: haMode
    }
    network: {
      delegatedSubnetResourceId: pgSubnetId
    }
    authentication: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Enabled'
    }
  }
}

// Configuration: enable pg_uuidv7 extension in azure.extensions
resource postgresConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview' = {
  parent: postgresServer
  name: 'azure.extensions'
  properties: {
    value: 'pg_uuidv7'
    source: 'user-override'
  }
}

@description('Postgres server resource ID')
output serverId string = postgresServer.id

@description('Postgres server FQDN')
output serverFqdn string = postgresServer.properties.fullyQualifiedDomainName
