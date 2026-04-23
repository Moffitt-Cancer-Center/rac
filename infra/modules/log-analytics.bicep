@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('Name for the Log Analytics workspace')
param workspaceName string

@description('Name for the Application Insights component')
param componentName string

@description('Log Analytics retention in days')
param retentionDays int = 30

@description('Resource tags applied to all resources')
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionDays
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: componentName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    RetentionInDays: retentionDays
  }
}

@description('Log Analytics workspace resource ID')
output workspaceId string = workspace.id

@description('Log Analytics workspace customer ID')
output workspaceCustomerId string = workspace.properties.customerId

@description('Application Insights connection string')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

@description('Application Insights resource ID')
output appInsightsId string = appInsights.id
