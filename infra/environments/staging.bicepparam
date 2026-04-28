using '../main.bicep'

// STAGING ENVIRONMENT: GeneralPurpose Postgres, same-zone HA, zone-redundant ACA
param racEnv = 'staging'
param parentDomain = 'rac-staging.example.org'
param location = 'eastus'
param idpTenantId = '00000000-0000-0000-0000-000000000000'
param acrName = 'racstagingacr001'
param storageAccountName = 'racstagingst001'
param pgServerName = 'rac-staging-pg'
param pgAdminPassword = readEnvironmentVariable('RAC_PG_ADMIN_PASSWORD')
param appGwTlsCertKvSecretId = readEnvironmentVariable('RAC_APPGW_TLS_CERT_KV_SECRET_ID')
param controlPlaneIdentityPrincipalId = ''
param vnetOctet = 20
param pgSkuName = 'Standard_D2s_v3'
param pgSkuTier = 'GeneralPurpose'
param pgStorageSizeGB = 64
param pgHaMode = 'SameZone'
param pgBackupRetentionDays = 14
param acaZoneRedundant = true
param acaProfileSku = 'Consumption'
param actionGroupEmails = []
param actionGroupWebhookUri = ''
param shimAppId = ''
param controlPlaneAppId = ''
param pipelineTimeoutMinutes = 120

// ===== Phase 1+2: Pipeline Trust (codified, NOT deployed in this plan's scope) =====
// Per design plan DoD item 3: staging is "codified in bicep but not deployed".
// These stubs allow `az deployment sub validate` against staging to succeed.
// A future plan provisions the rac-pipeline-staging app reg + flips the gate.
param deployPipelineKv = false
param deployPipelineIdentity = false
param pipelineAppUniqueNameDev = ''
param pipelineAppPrincipalIdDev = ''
param pipelineAppClientIdDev = ''
