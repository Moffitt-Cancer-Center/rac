using '../main.bicep'

// PRODUCTION ENVIRONMENT: GeneralPurpose Postgres, zone-redundant HA, D4 dedicated ACA profile
param racEnv = 'prod'
param parentDomain = 'rac.example.org'
param location = 'eastus'
param idpTenantId = '00000000-0000-0000-0000-000000000000'
param acrName = 'racprodacr001'
param storageAccountName = 'racprodst001'
param pgServerName = 'rac-prod-pg'
param pgAdminPassword = readEnvironmentVariable('RAC_PG_ADMIN_PASSWORD')
param appGwTlsCertKvSecretId = readEnvironmentVariable('RAC_APPGW_TLS_CERT_KV_SECRET_ID')
param controlPlaneIdentityPrincipalId = ''
param vnetOctet = 30
param pgSkuName = 'Standard_D4s_v3'
param pgSkuTier = 'GeneralPurpose'
param pgStorageSizeGB = 128
param pgHaMode = 'ZoneRedundant'
param pgBackupRetentionDays = 35
param acaZoneRedundant = true
param acaProfileSku = 'D4'
param actionGroupEmails = []
param actionGroupWebhookUri = ''
param shimAppId = ''
param controlPlaneAppId = ''
param pipelineTimeoutMinutes = 120
