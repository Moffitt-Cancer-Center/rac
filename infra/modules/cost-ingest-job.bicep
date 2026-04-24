// Microsoft.App/jobs scheduled job for the nightly cost export ingestion.
// Runs at 03:00 UTC daily (after the graph sweep at 02:00 UTC).
//
// Verifies: rac-v1.AC11.2

@description('Azure region for the job resource')
param location string

@description('Deployment environment: dev | staging | prod')
param racEnv string

@description('ACA managed environment resource ID')
param managedEnvironmentId string

@description('Full image reference, e.g. acr.azurecr.io/rac-control-plane:latest')
param imageName string

@description('User-assigned managed identity resource ID')
param managedIdentityResourceId string

@description('ACR login server, e.g. acr.azurecr.io')
param registryServer string

@description('Resource tags')
param tags object

resource costIngestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'rac-cost-ingest-${racEnv}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    environmentId: managedEnvironmentId
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 3600
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: '0 3 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: []
      registries: [
        {
          server: registryServer
          identity: managedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'cost-ingest'
          image: imageName
          command: [
            'python'
          ]
          args: [
            '-m'
            'rac_control_plane.cli.cost_ingest'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: []  // Operator populates via ACA env var references in main.bicep
        }
      ]
    }
  }
}

@description('Resource ID of the scheduled job')
output jobId string = costIngestJob.id

@description('Name of the scheduled job')
output jobName string = costIngestJob.name
