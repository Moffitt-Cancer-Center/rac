targetScope = 'subscription'

import { buildTags } from 'modules/tags.bicep'

// ========== PARAMETERS ==========

@description('Deployment environment: dev | staging | prod')
@allowed(['dev', 'staging', 'prod'])
param racEnv string

@description('Parent DNS domain, e.g. rac.moffitt.org')
param parentDomain string

@description('Azure region for all resources')
param location string

@description('Entra tenant ID for OIDC issuer validation')
param idpTenantId string

// Globally-unique names default to a subscription-scoped hash so demo deploys
// in different subscriptions don't collide. Override in <env>.bicepparam only
// if you need a specific name (institutional branding, vanity URL, etc.).
// Hash is trimmed to 10 chars so the storage account name stays within its
// 24-char limit even for longer env names like "staging" (rac+staging+st+10=22).
@description('Globally unique ACR name (3-50 alphanumeric, no hyphens)')
param acrName string = 'rac${racEnv}acr${substring(uniqueString(subscription().subscriptionId, racEnv), 0, 10)}'

@description('Globally unique Storage account name (3-24 lowercase alphanumeric)')
param storageAccountName string = 'rac${racEnv}st${substring(uniqueString(subscription().subscriptionId, racEnv), 0, 10)}'

@description('Globally unique Postgres server name (hyphens allowed)')
param pgServerName string = 'rac-${racEnv}-pg-${substring(uniqueString(subscription().subscriptionId, racEnv), 0, 10)}'

@description('Postgres admin password. MUST come from Key Vault reference in bicepparam; never inline.')
@secure()
param pgAdminPassword string

@description('App Gateway TLS certificate Key Vault secret ID (full versioned secret URI)')
@secure()
param appGwTlsCertKvSecretId string

@description('Control Plane managed identity principal ID (empty on first deploy; populated in Phase 5 Task 1 re-deploy)')
param controlPlaneIdentityPrincipalId string = ''

@description('VNet third octet (10/20/30 for dev/staging/prod)')
param vnetOctet int

@description('Postgres sizing: SKU name')
param pgSkuName string

@description('Postgres sizing: SKU tier')
param pgSkuTier string

@description('Postgres sizing: storage size in GB')
param pgStorageSizeGB int

@description('Postgres sizing: HA mode (Disabled, SameZone, ZoneRedundant)')
param pgHaMode string

@description('Postgres sizing: backup retention days')
param pgBackupRetentionDays int

@description('ACA environment: zone redundancy (dev: false, staging/prod: true)')
param acaZoneRedundant bool

@description('ACA environment: workload profile SKU (Consumption or D4)')
param acaProfileSku string

@description('Alert action group — email recipients (comma-separated oncall addresses)')
param actionGroupEmails array = []

@description('Alert action group — optional webhook URI (PagerDuty/ServiceNow). Leave empty to skip.')
param actionGroupWebhookUri string = ''

@description('Shim ACA app resource ID for alerts (empty on first deploy; populate after Phase 6)')
param shimAppId string = ''

@description('Control Plane ACA app resource ID for alerts (empty on first deploy; populate after Phase 2)')
param controlPlaneAppId string = ''

@description('Pipeline timeout in minutes (used to compute stuck-pipeline alert threshold). Max 180 min due to Azure 360-min schema limit (2x multiplier for alert window).')
@maxValue(180)
param pipelineTimeoutMinutes int = 120

@description('Key Vault purge protection (false for dev, true for staging/prod)')
param kvEnablePurgeProtection bool = true

@description('Control Plane container image reference for scheduled jobs (e.g. acr.azurecr.io/rac-control-plane:latest). Leave empty to skip graph-sweep-job deployment.')
param controlPlaneImageName string = ''

@description('Key Vault soft-delete retention in days (7 for dev, 90 for staging/prod)')
@minValue(7)
@maxValue(90)
param kvSoftDeleteRetentionInDays int = 90

@description('Shim container image reference (e.g. racdevacr001.azurecr.io/rac-shim:v1.0). Leave empty on first deploy — shim ACA app is skipped until the image is pushed.')
param shimImageName string = ''

@description('OIDC issuer URI for reviewer token validation (required when shimImageName is set)')
param shimIssuer string = ''

@description('Cookie domain for rac_session cookie (required when shimImageName is set, typically .{parentDomain})')
param shimCookieDomain string = ''

@description('Institution display name for shim branded pages')
param shimInstitutionName string = ''

@description('Optional institution brand logo URL for shim branded pages')
param shimBrandLogoUrl string = ''

@description('Enable shim OpenTelemetry/Prometheus metrics')
param shimMetricsEnabled bool = true

@description('OTLP exporter endpoint for the shim (leave empty to disable OTLP export)')
param shimOtlpEndpoint string = ''

// ========== VARIABLES ==========

var commonTags = buildTags(racEnv, {})
var kvName = 'kv-rac-${uniqueString(subscription().id)}-${racEnv}'
var workspaceName = 'la-rac-${uniqueString(subscription().id)}-${racEnv}'
var componentName = 'ai-rac-${uniqueString(subscription().id)}-${racEnv}'
var appGwName = 'appgw-rac-${racEnv}'

// ========== RESOURCE GROUPS ==========

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: 'rg-rac-${racEnv}'
  location: location
  tags: commonTags
}

resource rgTier3 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: 'rg-rac-tier3-${racEnv}'
  location: location
  tags: union(commonTags, { rac_managed_by: 'rac-control-plane' })
}

// ========== CORE INFRASTRUCTURE MODULES ==========

module network 'modules/network.bicep' = {
  scope: rg
  name: 'deploy-network'
  params: {
    location: location
    racEnv: racEnv
    vnetOctet: vnetOctet
    tags: commonTags
  }
}

module logAnalytics 'modules/log-analytics.bicep' = {
  scope: rg
  name: 'deploy-loganalytics'
  params: {
    location: location
    workspaceName: workspaceName
    componentName: componentName
    retentionDays: 30
    tags: commonTags
  }
}

module keyVault 'modules/key-vault.bicep' = {
  scope: rg
  name: 'deploy-keyvault'
  params: {
    location: location
    kvName: kvName
    tenantId: idpTenantId
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    enablePurgeProtection: kvEnablePurgeProtection
    softDeleteRetentionInDays: kvSoftDeleteRetentionInDays
    tags: commonTags
  }
}

module blobStorage 'modules/blob-storage.bicep' = {
  scope: rg
  name: 'deploy-blobstorage'
  params: {
    location: location
    storageAccountName: storageAccountName
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    sku: (racEnv == 'dev') ? 'Standard_LRS' : 'Standard_GRS'
    tags: commonTags
  }
}

module postgres 'modules/postgres.bicep' = {
  scope: rg
  name: 'deploy-postgres'
  params: {
    location: location
    serverName: pgServerName
    adminPassword: pgAdminPassword
    skuName: pgSkuName
    skuTier: pgSkuTier
    storageSizeGB: pgStorageSizeGB
    haMode: pgHaMode
    backupRetentionDays: pgBackupRetentionDays
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    extensions: ['pg_uuidv7']
    geoRedundantBackup: (racEnv == 'dev') ? 'Disabled' : 'Enabled'
    tags: commonTags
  }
}

module acr 'modules/acr.bicep' = {
  scope: rg
  name: 'deploy-acr'
  params: {
    location: location
    racEnv: racEnv
    acrName: acrName
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    tags: commonTags
  }
}

module acaEnvironment 'modules/aca-env.bicep' = {
  scope: rg
  name: 'deploy-acaenv'
  params: {
    location: location
    envName: 'aca-rac-${racEnv}'
    acaSubnetId: network.outputs.acaSubnetId
    workspaceCustomerId: logAnalytics.outputs.workspaceCustomerId
    workspaceId: logAnalytics.outputs.workspaceId
    zoneRedundant: acaZoneRedundant
    profileSku: acaProfileSku
    tags: commonTags
  }
}

module dnsZone 'modules/dns-zone.bicep' = {
  scope: rg
  name: 'deploy-dnszone'
  params: {
    parentDomain: parentDomain
    controlPlaneIdentityPrincipalId: controlPlaneIdentityPrincipalId
    tags: commonTags
  }
}

// Cross-RG role assignment: grant the App Gateway MI access to the bootstrap
// KV (where the TLS cert lives). MUST run before appGateway so the gateway
// can read the cert at provisioning time.
module bootstrapKvRbac 'modules/bootstrap-kv-rbac.bicep' = {
  scope: resourceGroup('rg-rac-bootstrap')
  name: 'deploy-bootstrap-kv-rbac'
  params: {
    appGwMiPrincipalId: managedIdentity.outputs.appGwMiPrincipalId
  }
}

module appGateway 'modules/app-gateway.bicep' = {
  scope: rg
  name: 'deploy-appgw'
  // Wait for the App Gateway MI to be granted KV access on both the platform
  // KV (cert metadata) and the bootstrap KV (where the cert actually lives).
  dependsOn: [
    bootstrapKvRbac
    roleAssignmentsKv
  ]
  params: {
    location: location
    racEnv: racEnv
    appGwName: appGwName
    appGwSubnetId: network.outputs.appGwSubnetId
    parentDomain: parentDomain
    tlsCertKvSecretId: appGwTlsCertKvSecretId
    appGwMiResourceId: managedIdentity.outputs.appGwMiResourceId
    // When the shim module ran on a previous deploy, the shim FQDN is available
    // as an output.  Pass it here so the backend pool targets the shim.
    // On first deploy (shimImageName empty) shimFqdn defaults to '' and the
    // placeholder address is preserved in app-gateway.bicep.
    // Null-safe access: shimAcaApp is a conditional module, so use the
    // safe-dereference operator to avoid BCP318 ("module|null may be null at
    // start of deploy"). When the module isn't deployed, fall back to ''.
    shimFqdn: shimAcaApp.?outputs.shimFqdn ?? ''
    tags: commonTags
  }
}

module frontDoor 'modules/front-door.bicep' = {
  scope: rg
  name: 'deploy-frontdoor'
  params: {
    racEnv: racEnv
    parentDomain: parentDomain
    appGatewayPublicFqdn: appGateway.outputs.appGatewayPublicFqdn
    appGatewayPrivateLinkResourceId: ''
    tags: commonTags
  }
}

// ========== TIER 3 & OBSERVABILITY MODULES ==========

module managedIdentity 'modules/managed-identity.bicep' = {
  scope: rg
  name: 'deploy-managedidentity'
  params: {
    location: location
    racEnv: racEnv
    tags: commonTags
  }
}

// ========== ROLE ASSIGNMENTS ==========
// Module invocation 1: scoped to platform RG for Key Vault role assignments
module roleAssignmentsKv 'modules/role-assignments.bicep' = {
  scope: rg
  name: 'deploy-roleassignments-kv'
  params: {
    controlPlaneMiPrincipalId: managedIdentity.outputs.controlPlaneMiPrincipalId
    shimMiPrincipalId: managedIdentity.outputs.shimMiPrincipalId
    appGwMiPrincipalId: managedIdentity.outputs.appGwMiPrincipalId
    kvResourceId: keyVault.outputs.kvId
  }
}

// Module invocation 2: scoped to Tier 3 RG for Contributor role assignment
// (Phase 5 conditional: only assigns when Control Plane identity is available)
module roleAssignmentsTier3 'modules/role-assignments.bicep' = {
  scope: rgTier3
  name: 'deploy-roleassignments-tier3'
  params: {
    controlPlaneMiPrincipalId: managedIdentity.outputs.controlPlaneMiPrincipalId
    shimMiPrincipalId: ''
    kvResourceId: ''
  }
}

module alerts 'modules/alerts.bicep' = {
  scope: rg
  name: 'deploy-alerts'
  params: {
    location: location
    racEnv: racEnv
    actionGroupEmails: actionGroupEmails
    actionGroupWebhookUri: actionGroupWebhookUri
    shimAppId: shimAppId
    controlPlaneAppId: controlPlaneAppId
    postgresServerId: postgres.outputs.serverId
    kvId: keyVault.outputs.kvId
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    pipelineTimeoutMinutes: pipelineTimeoutMinutes
    tags: commonTags
  }
}

// ========== PHASE 5: GRAPH SWEEP SCHEDULED JOB ==========
// Deploys only when controlPlaneImageName is provided (post-Phase-5 re-deploy).
// The job runs nightly at 02:00 UTC to detect deactivated PIs (AC9.2).
module graphSweepJob 'modules/graph-sweep-job.bicep' = if (!empty(controlPlaneImageName)) {
  scope: rg
  name: 'deploy-graph-sweep-job'
  params: {
    location: location
    racEnv: racEnv
    managedEnvironmentId: acaEnvironment.outputs.envId
    imageName: controlPlaneImageName
    managedIdentityResourceId: managedIdentity.outputs.controlPlaneMiResourceId
    registryServer: acr.outputs.acrLoginServer
    tags: commonTags
  }
}

// ========== PHASE 5: COST INGEST SCHEDULED JOB ==========
// Deploys only when controlPlaneImageName is provided (post-Phase-5 re-deploy).
// Runs nightly at 03:00 UTC to ingest Azure Cost Management export blobs (AC11.2).
module costIngestJob 'modules/cost-ingest-job.bicep' = if (!empty(controlPlaneImageName)) {
  scope: rg
  name: 'deploy-cost-ingest-job'
  params: {
    location: location
    racEnv: racEnv
    managedEnvironmentId: acaEnvironment.outputs.envId
    imageName: controlPlaneImageName
    managedIdentityResourceId: managedIdentity.outputs.controlPlaneMiResourceId
    registryServer: acr.outputs.acrLoginServer
    tags: commonTags
  }
}

// ========== PHASE 6: SHIM ACA APP ==========
// Deploys only when shimImageName is provided (post-Phase-6 re-deploy).
// The shim is the single public entry point; min-replicas=1 enforced in the module.
module shimAcaApp 'modules/shim-aca-app.bicep' = if (!empty(shimImageName)) {
  scope: rg
  name: 'deploy-shim-aca-app'
  params: {
    location: location
    racEnv: racEnv
    managedEnvironmentId: acaEnvironment.outputs.envId
    shimMiResourceId: managedIdentity.outputs.shimMiResourceId
    imageName: shimImageName
    registryServer: acr.outputs.acrLoginServer
    kvUri: keyVault.outputs.kvUri
    parentDomain: parentDomain
    issuer: shimIssuer
    cookieDomain: shimCookieDomain
    acaInternalSuffix: acaEnvironment.outputs.envDefaultDomain
    institutionName: shimInstitutionName
    brandLogoUrl: shimBrandLogoUrl
    metricsEnabled: shimMetricsEnabled
    otlpEndpoint: shimOtlpEndpoint
    tags: commonTags
  }
}

module eventHub 'modules/event-hub.bicep' = {
  scope: rg
  name: 'deploy-eventhub'
  params: {
    location: location
    racEnv: racEnv
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    tags: commonTags
  }
}

// ========== OUTPUTS ==========

@description('Tier 2 platform resource group name')
output resourceGroupName string = rg.name

@description('Tier 3 dynamic apps resource group name')
output tier3ResourceGroupName string = rgTier3.name

@description('ACR login server')
output acrLoginServer string = acr.outputs.acrLoginServer

@description('Key Vault URI')
output keyVaultUri string = keyVault.outputs.kvUri

@description('ACA environment default domain')
output acaEnvDefaultDomain string = acaEnvironment.outputs.envDefaultDomain

@description('App Gateway public FQDN')
output appGatewayPublicFqdn string = appGateway.outputs.appGatewayPublicFqdn

@description('Front Door endpoint hostname')
output frontDoorEndpointHostname string = frontDoor.outputs.frontDoorEndpointHostname

@description('DNS zone nameservers (for parent delegation)')
output dnsZoneNameServers array = dnsZone.outputs.zoneNameServers

@description('Control Plane managed identity principal ID')
output controlPlaneMiPrincipalId string = managedIdentity.outputs.controlPlaneMiPrincipalId

@description('Shim managed identity principal ID')
output shimMiPrincipalId string = managedIdentity.outputs.shimMiPrincipalId

@description('Event Hub namespace resource ID')
output eventHubNamespaceId string = eventHub.outputs.eventHubNamespaceId

@description('Shim ACA app resource ID (empty when shimImageName was not provided on this deploy)')
output shimAppId string = shimAcaApp.?outputs.shimAppId ?? ''

@description('Shim ACA internal FQDN (empty when shimImageName was not provided on this deploy)')
output shimFqdn string = shimAcaApp.?outputs.shimFqdn ?? ''
