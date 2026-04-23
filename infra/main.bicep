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

@description('Globally unique ACR name (3-50 alphanumeric)')
param acrName string

@description('Globally unique Storage account name (3-24 lowercase alphanumeric)')
param storageAccountName string

@description('Globally unique Postgres server name')
param pgServerName string

@description('Postgres admin password. MUST come from Key Vault reference in bicepparam; never inline.')
@secure()
param pgAdminPassword string

@description('App Gateway TLS certificate Key Vault secret ID (full versioned secret URI)')
@secure()
param appGwTlsCertKvSecretId string

@description('Control Plane managed identity principal ID (empty on first deploy; populated in Phase 5 Task 1 re-deploy)')
param controlPlaneIdentityPrincipalId string = ''

@description('Shim managed identity principal ID (empty on first deploy; populated in Phase 6)')
param shimMiPrincipalId string = ''

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

@description('Pipeline timeout in minutes (used to compute stuck-pipeline alert threshold)')
param pipelineTimeoutMinutes int = 120

@description('Key Vault purge protection (false for dev, true for staging/prod)')
param kvEnablePurgeProtection bool = true

@description('Key Vault soft-delete retention in days (7 for dev, 90 for staging/prod)')
@minValue(7)
@maxValue(90)
param kvSoftDeleteRetentionInDays int = 90

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
    racEnv: racEnv
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
    racEnv: racEnv
    kvName: kvName
    tenantId: idpTenantId
    peSubnetId: network.outputs.peSubnetId
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
    racEnv: racEnv
    storageAccountName: storageAccountName
    peSubnetId: network.outputs.peSubnetId
    sku: (racEnv == 'dev') ? 'Standard_LRS' : 'Standard_GRS'
    tags: commonTags
  }
}

module postgres 'modules/postgres.bicep' = {
  scope: rg
  name: 'deploy-postgres'
  params: {
    location: location
    racEnv: racEnv
    serverName: pgServerName
    adminPassword: pgAdminPassword
    skuName: pgSkuName
    skuTier: pgSkuTier
    storageSizeGB: pgStorageSizeGB
    haMode: pgHaMode
    backupRetentionDays: pgBackupRetentionDays
    pgSubnetId: network.outputs.pgSubnetId
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
    tags: commonTags
  }
}

module acaEnvironment 'modules/aca-env.bicep' = {
  scope: rg
  name: 'deploy-acaenv'
  params: {
    location: location
    racEnv: racEnv
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

module appGateway 'modules/app-gateway.bicep' = {
  scope: rg
  name: 'deploy-appgw'
  params: {
    location: location
    racEnv: racEnv
    appGwName: appGwName
    appGwSubnetId: network.outputs.appGwSubnetId
    parentDomain: parentDomain
    tlsCertKvSecretId: appGwTlsCertKvSecretId
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
