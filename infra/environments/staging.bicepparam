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
