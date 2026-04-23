@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('Email addresses for alert action group (comma-separated)')
param actionGroupEmails array = []

@description('Webhook URI for alert action group (optional)')
param actionGroupWebhookUri string = ''

@description('Shim ACA app resource ID (empty on first deploy)')
param shimAppId string = ''

@description('Control Plane ACA app resource ID (empty on first deploy)')
param controlPlaneAppId string = ''

@description('Postgres Flexible Server resource ID')
param postgresServerId string

@description('Key Vault resource ID')
param kvId string

@description('Log Analytics workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Pipeline timeout in minutes (used for stuck-pipeline alert threshold). Max 180 min due to Azure 360-min schema limit (2x multiplier for alert window).')
@maxValue(180)
param pipelineTimeoutMinutes int = 120

@description('Resource tags applied to all resources')
param tags object

// Action Group
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-rac-${racEnv}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'rac-${racEnv}'
    enabled: true
    emailReceivers: [
      for (email, index) in actionGroupEmails: {
        name: 'email-${index}'
        emailAddress: email
        useCommonAlertSchema: true
      }
    ]
    webhookReceivers: !empty(actionGroupWebhookUri) ? [
      {
        name: 'webhook-receiver'
        serviceUri: actionGroupWebhookUri
        useCommonAlertSchema: true
      }
    ] : []
  }
}

// Alert: Shim 5xx error rate > 1% (via scheduled query rule on Log Analytics)
resource alertShim5xx 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = if (!empty(shimAppId)) {
  name: 'alert-shim-5xx-${racEnv}'
  location: location
  tags: tags
  properties: {
    description: 'Shim application 5xx error rate > 1% over 5 minutes'
    enabled: true
    severity: 2
    scopes: [
      logAnalyticsWorkspaceId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      allOf: [
        {
          query: '''
          ContainerAppConsoleLogs_CL
          | where ContainerAppName_s == "shim"
          | summarize total = count(), fivexx = countif(StatusCode_d >= 500) by bin(TimeGenerated, 1m)
          | extend rate = fivexx * 100.0 / total
          | where rate > 1.0 and total > 20
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// Alert: Control Plane 5xx error rate > 1% (via scheduled query rule on Log Analytics)
resource alertControlPlane5xx 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = if (!empty(controlPlaneAppId)) {
  name: 'alert-controlplane-5xx-${racEnv}'
  location: location
  tags: tags
  properties: {
    description: 'Control Plane application 5xx error rate > 1% over 5 minutes'
    enabled: true
    severity: 2
    scopes: [
      logAnalyticsWorkspaceId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      allOf: [
        {
          query: '''
          ContainerAppConsoleLogs_CL
          | where ContainerAppName_s == "controlplane"
          | summarize total = count(), fivexx = countif(StatusCode_d >= 500) by bin(TimeGenerated, 1m)
          | extend rate = fivexx * 100.0 / total
          | where rate > 1.0 and total > 20
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// Alert: Postgres connection failures > 0 over 5 minutes
resource alertPostgresConnectionFailures 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-postgres-connection-failures-${racEnv}'
  location: 'global'
  tags: tags
  properties: {
    description: 'Postgres connection failures > 0 over 5 minutes'
    enabled: true
    severity: 1
    scopes: [
      postgresServerId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'connection-failures'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'connections_failed'
          metricNamespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
        webHookProperties: {}
      }
    ]
  }
}

// Alert: Key Vault access denied > 0 over 5 minutes
resource alertKeyVaultAccessDenied 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-keyvault-access-denied-${racEnv}'
  location: 'global'
  tags: tags
  properties: {
    description: 'Key Vault access denied errors > 0 over 5 minutes'
    enabled: true
    severity: 1
    scopes: [
      kvId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'access-denied'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'ServiceApiResult'
          metricNamespace: 'Microsoft.KeyVault/vaults'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          dimensions: [
            {
              name: 'ResultType'
              operator: 'Include'
              values: [
                'Forbidden'
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
        webHookProperties: {}
      }
    ]
  }
}

// Alert: Pipeline workflow stuck (Log Analytics scheduled query)
// Alert triggers when no terminal verdict callback observed within 2x pipeline timeout
resource alertPipelineStuck 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-pipeline-stuck-${racEnv}'
  location: location
  tags: tags
  properties: {
    description: 'Pipeline workflow stuck > 2x timeout without terminal verdict'
    enabled: true
    severity: 2
    scopes: [
      logAnalyticsWorkspaceId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT${2 * pipelineTimeoutMinutes}M'
    criteria: {
      allOf: [
        {
          query: '''
          let window_start = ago(${2 * pipelineTimeoutMinutes}m);
          let started = RAC_PipelineLog_CL
            | where TimeGenerated between (window_start .. now())
            | where event_type_s == "pipeline_started"
            | project correlation_id_s, started_at = TimeGenerated;
          let verdicts = RAC_PipelineLog_CL
            | where TimeGenerated between (window_start .. now())
            | where event_type_s == "pipeline_verdict"
            | project correlation_id_s;
          started
          | where correlation_id_s !in (verdicts)
          | where started_at < ago(${pipelineTimeoutMinutes}m)
          | summarize stuck_count = count()
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

@description('Action Group resource ID')
output actionGroupId string = actionGroup.id
