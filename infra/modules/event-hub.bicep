@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('Log Analytics workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Resource tags applied to all resources')
param tags object

@description('Whether to provision Log Analytics → Event Hub data exports. Defaults to false because (a) the custom tables RAC_AccessLog_CL / RAC_ApprovalEvent_CL do not exist on first deploy until ingestion creates them, and (b) the dataExports feature is gated at subscription level on some personal/trial subs. Flip to true after first ingestion has populated the custom tables.')
param deployDataExports bool = false

// Event Hub Namespace
resource eventHubNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: 'evhns-rac-${racEnv}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
    capacity: 1
  }
  properties: {
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    zoneRedundant: false
  }
}

// Event Hub: Access Logs
resource accessLogsHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubNamespace
  name: 'eh-rac-access-logs'
  properties: {
    messageRetentionInDays: 1
    partitionCount: 1
  }
}

// Event Hub: Approval Events
resource approvalEventsHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubNamespace
  name: 'eh-rac-approval-events'
  properties: {
    messageRetentionInDays: 1
    partitionCount: 1
  }
}

// Authorization Rule: Listen only (for SIEM consumers)
resource listenerAuthRule 'Microsoft.EventHub/namespaces/authorizationRules@2024-01-01' = {
  parent: eventHubNamespace
  name: 'rac-siem-listener'
  properties: {
    rights: [
      'Listen'
    ]
  }
}

// Reference to the Log Analytics workspace
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsWorkspaceId, '/'))
}

// Data Export: Forward access logs to event hub
resource accessLogsDataExport 'Microsoft.OperationalInsights/workspaces/dataExports@2020-08-01' = if (deployDataExports) {
  parent: workspace
  name: 'dataexport-accesslogs-${racEnv}'
  properties: {
    destination: {
      resourceId: eventHubNamespace.id
      metaData: {
        eventHubName: accessLogsHub.name
      }
    }
    tableNames: [
      'RAC_AccessLog_CL'
    ]
    enable: true
  }
}

// Data Export: Forward approval events to event hub
resource approvalEventsDataExport 'Microsoft.OperationalInsights/workspaces/dataExports@2020-08-01' = if (deployDataExports) {
  parent: workspace
  name: 'dataexport-approval-${racEnv}'
  properties: {
    destination: {
      resourceId: eventHubNamespace.id
      metaData: {
        eventHubName: approvalEventsHub.name
      }
    }
    tableNames: [
      'RAC_ApprovalEvent_CL'
    ]
    enable: true
  }
}

@description('Event Hub Namespace resource ID')
output eventHubNamespaceId string = eventHubNamespace.id

@description('Access Logs Event Hub resource ID')
output accessLogsEventHubId string = accessLogsHub.id

@description('Approval Events Event Hub resource ID')
output approvalEventsEventHubId string = approvalEventsHub.id

@description('Listener authorization rule connection string secret reference for Key Vault')
output listenerConnectionStringSecretRef string = 'eh-listener-connstring-${racEnv}'
