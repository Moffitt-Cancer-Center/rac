using '../main.bicep'

// DEV ENVIRONMENT: Burstable Postgres, minimal redundancy, dev-friendly KV settings
param racEnv = 'dev'
param parentDomain = 'rac-dev.example.org'
param location = 'eastus'
param idpTenantId = '00000000-0000-0000-0000-000000000000'
param acrName = 'racdevacr001'
param storageAccountName = 'racdevst001'
param pgServerName = 'rac-dev-pg'
param pgAdminPassword = getSecret('<subscription-id>', '<bootstrap-rg-name>', '<bootstrap-kv-name>', 'pg-admin-password-dev')
param appGwTlsCertKvSecretId = 'https://<bootstrap-kv-name>.vault.azure.net/secrets/appgw-cert-dev/<version>'
param controlPlaneIdentityPrincipalId = ''
param vnetOctet = 10
param pgSkuName = 'Standard_B2s'
param pgSkuTier = 'Burstable'
param pgStorageSizeGB = 32
param pgHaMode = 'Disabled'
param pgBackupRetentionDays = 7
param acaZoneRedundant = false
param acaProfileSku = 'Consumption'
param actionGroupEmails = []
param actionGroupWebhookUri = ''
param shimAppId = ''
param controlPlaneAppId = ''
param pipelineTimeoutMinutes = 120
param kvEnablePurgeProtection = false
param kvSoftDeleteRetentionInDays = 7
