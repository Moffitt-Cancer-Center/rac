// Microsoft.App/jobs scheduled job for the nightly Graph sweep.
// Runs at 02:00 UTC daily.  Uses the Control Plane image + MI.
//
// Verifies: rac-v1.AC9.2

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

resource graphSweepJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'rac-graph-sweep-${racEnv}'
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
        cronExpression: '0 2 * * *'
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
          name: 'graph-sweep'
          image: imageName
          command: [
            'python'
          ]
          args: [
            '-m'
            'rac_control_plane.cli.graph_sweep'
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
output jobId string = graphSweepJob.id

@description('Name of the scheduled job')
output jobName string = graphSweepJob.name
