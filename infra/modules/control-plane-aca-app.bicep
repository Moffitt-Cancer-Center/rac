// Control Plane ACA app — the FastAPI backend that owns submission intake,
// approvals, Tier 3 provisioning, reviewer tokens, and the SPA hosting.
//
// Deployed behind App Gateway (which fronts the public *.{parentDomain})
// with the Shim sitting between App Gateway and researcher Tier 3 apps.
// The Control Plane itself is reachable via App Gateway path-routing or
// host-based routing on a control-plane.{parentDomain} hostname.
//
// Smoke-test posture (2026-04-26): connects to Postgres as the admin role
// (`rac_admin`) using the bootstrap-KV admin password. The full role-
// separated `rac_app` posture is deferred to a follow-up that adds a
// migrate ACA Job + a platform-KV `rac-app-db-password` secret.

@description('Azure region for the app resource')
param location string

@description('Deployment environment: dev | staging | prod')
param racEnv string

@description('ACA managed environment resource ID')
param managedEnvironmentId string

@description('Control Plane user-assigned managed identity resource ID')
param controlPlaneMiResourceId string

@description('Control Plane user-assigned managed identity client ID (used by DefaultAzureCredential inside the container)')
param controlPlaneMiClientId string

@description('Full image reference, e.g. racdevacrczo2xbgcnq.azurecr.io/rac-control-plane:dev-001')
param imageName string

@description('ACR login server, e.g. racdevacrczo2xbgcnq.azurecr.io')
param registryServer string

@description('Platform Key Vault URI, e.g. https://kv-rac-xxxx-dev.vault.azure.net/')
param kvUri string

@description('Postgres server FQDN, e.g. rac-dev-pg-xxx.postgres.database.azure.com')
param pgHost string

@description('Postgres application database name (e.g. rac)')
param pgDatabase string

@description('Postgres login user (smoke-test default: rac_admin from bootstrap)')
param pgUser string

@description('Name of the Key Vault secret holding the Postgres password (lives in platform KV; operator seeds before deploy by copying from bootstrap KV).')
param pgPasswordSecretName string = 'rac-pg-admin-password'

@description('Parent DNS domain, e.g. rac-dev.rac.checkwithscience.com')
param parentDomain string

@description('Institution display name, surfaced in the SPA and emails')
param institutionName string

@description('Optional brand logo URL')
param brandLogoUrl string = ''

@description('Entra tenant ID for OIDC')
param idpTenantId string

@description('Entra app registration client ID for the user-facing OIDC flow')
param idpClientId string

@description('Entra app registration client ID for the API / client-credentials flow')
param idpApiClientId string

@description('Tier 3 dynamic apps resource group name')
param tier3ResourceGroupName string

@description('Subscription ID where Tier 3 apps live')
param subscriptionId string = subscription().subscriptionId

@description('DNS zone name (parent zone the control plane provisions records under)')
param dnsZoneName string

@description('Storage account name where researcher uploads are staged')
param storageAccountName string

@description('Blob account URL for the storage account')
param blobAccountUrl string

@description('Files-storage account key KV secret name (for ACA volume mounts on Tier 3)')
param filesStorageAccountKeySecretName string = 'files-storage-account-key'

@description('ACR login server (also passed as RAC_ACR_LOGIN_SERVER env)')
param acrLoginServer string

@description('ACA environment resource ID (for Tier 3 provisioning)')
param acaEnvResourceId string

@description('App Gateway public IP (informational, used by control plane to render researcher URLs)')
param appGatewayPublicIp string = ''

@description('Scan severity gate threshold for approvals')
@allowed(['critical', 'high', 'medium', 'low'])
param scanSeverityGate string = 'high'

@description('Approver role name for the research/leadership stage')
param approverRoleResearch string = 'rac-approver-research'

@description('Approver role name for the IT stage')
param approverRoleIt string = 'rac-approver-it'

@description('Pipeline timeout in minutes')
param pipelineTimeoutMinutes int = 120

@description('GitHub owner of the rac-pipeline repo')
param githubPipelineOwner string = ''

@description('GitHub repo name for the pipeline')
param githubPipelineRepo string = 'rac-pipeline'

@description('Enable Prometheus/OpenTelemetry metrics endpoint')
param metricsEnabled bool = true

@description('OTLP exporter endpoint (leave empty to disable OTLP export)')
param otlpEndpoint string = ''

@description('Resource tags')
param tags object

resource controlPlaneApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rac-control-plane-${racEnv}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${controlPlaneMiResourceId}': {}
    }
  }
  properties: {
    environmentId: managedEnvironmentId
    configuration: {
      // Internal ingress: App Gateway is the public boundary.
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
      }
      registries: [
        {
          server: registryServer
          identity: controlPlaneMiResourceId
        }
      ]
      secrets: [
        {
          name: pgPasswordSecretName
          keyVaultUrl: '${kvUri}secrets/${pgPasswordSecretName}'
          identity: controlPlaneMiResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'control-plane'
          image: imageName
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(
            [
              {
                name: 'RAC_PG_PASSWORD'
                secretRef: pgPasswordSecretName
              }
              { name: 'RAC_ENV', value: racEnv }
              { name: 'RAC_INSTITUTION_NAME', value: institutionName }
              { name: 'RAC_PARENT_DOMAIN', value: parentDomain }
              { name: 'RAC_BRAND_LOGO_URL', value: brandLogoUrl }
              { name: 'RAC_IDP_TENANT_ID', value: idpTenantId }
              { name: 'RAC_IDP_CLIENT_ID', value: idpClientId }
              { name: 'RAC_IDP_API_CLIENT_ID', value: idpApiClientId }
              { name: 'RAC_PG_HOST', value: pgHost }
              { name: 'RAC_PG_PORT', value: '5432' }
              { name: 'RAC_PG_DB', value: pgDatabase }
              { name: 'RAC_PG_USER', value: pgUser }
              { name: 'RAC_PG_SSL_MODE', value: 'require' }
              { name: 'RAC_KV_URI', value: kvUri }
              { name: 'RAC_BLOB_ACCOUNT_URL', value: blobAccountUrl }
              { name: 'RAC_ACR_LOGIN_SERVER', value: acrLoginServer }
              { name: 'RAC_ACA_ENV_RESOURCE_ID', value: acaEnvResourceId }
              { name: 'RAC_SUBSCRIPTION_ID', value: subscriptionId }
              { name: 'RAC_RESOURCE_GROUP', value: tier3ResourceGroupName }
              { name: 'RAC_AZURE_LOCATION', value: location }
              { name: 'RAC_DNS_ZONE_NAME', value: dnsZoneName }
              { name: 'RAC_FILES_STORAGE_ACCOUNT_NAME', value: storageAccountName }
              { name: 'RAC_FILES_STORAGE_ACCOUNT_KEY_KV_SECRET_NAME', value: filesStorageAccountKeySecretName }
              { name: 'RAC_MANAGED_IDENTITY_RESOURCE_ID', value: controlPlaneMiResourceId }
              { name: 'RAC_CONTROLPLANE_MANAGED_IDENTITY_CLIENT_ID', value: controlPlaneMiClientId }
              { name: 'RAC_APP_GATEWAY_PUBLIC_IP', value: appGatewayPublicIp }
              { name: 'RAC_SCAN_SEVERITY_GATE', value: scanSeverityGate }
              { name: 'RAC_APPROVER_ROLE_RESEARCH', value: approverRoleResearch }
              { name: 'RAC_APPROVER_ROLE_IT', value: approverRoleIt }
              { name: 'RAC_PIPELINE_TIMEOUT_MINUTES', value: string(pipelineTimeoutMinutes) }
              { name: 'RAC_GH_PIPELINE_OWNER', value: githubPipelineOwner }
              { name: 'RAC_GH_PIPELINE_REPO', value: githubPipelineRepo }
              { name: 'RAC_METRICS_ENABLED', value: string(metricsEnabled) }
              {
                name: 'AZURE_CLIENT_ID'
                value: controlPlaneMiClientId
              }
            ],
            empty(otlpEndpoint) ? [] : [
              { name: 'RAC_OTLP_ENDPOINT', value: otlpEndpoint }
            ]
          )
        }
      ]
      // Control plane is not the public hot path — scale-to-zero is fine,
      // and acceptable cold-start latency is in the 5-10s range.
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

@description('Resource ID of the control plane Container App')
output controlPlaneAppId string = controlPlaneApp.id

@description('Internal FQDN of the control plane (used as App Gateway backend target if/when host-routed)')
output controlPlaneFqdn string = controlPlaneApp.properties.configuration.ingress.fqdn
