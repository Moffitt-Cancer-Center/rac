@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('ACA managed environment name')
param envName string

@description('ACA infrastructure subnet resource ID')
param acaSubnetId string

@description('Log Analytics workspace custom ID (customer ID)')
param workspaceCustomerId string

@description('Log Analytics workspace resource ID')
param workspaceId string

@description('Enable zone redundancy for ACA environment')
param zoneRedundant bool

@description('Workload profile SKU (e.g., Consumption, D4)')
param profileSku string

@description('Resource tags applied to all resources')
param tags object

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspaceCustomerId
        sharedKey: listKeys(workspaceId, '2023-09-01').primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
      internal: true
    }
    zoneRedundant: zoneRedundant
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
      {
        name: 'apps'
        workloadProfileType: profileSku
      }
    ]
  }
}

@description('ACA managed environment resource ID')
output envId string = managedEnvironment.id

@description('ACA default domain (internal FQDN)')
output envDefaultDomain string = managedEnvironment.properties.defaultDomain

@description('ACA static IP address')
output envStaticIp string = managedEnvironment.properties.staticIp
