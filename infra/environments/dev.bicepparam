using '../main.bicep'

// DEV ENVIRONMENT: Burstable Postgres, minimal redundancy, dev-friendly KV settings
param racEnv = 'dev'
param parentDomain = 'rac-dev.rac.checkwithscience.com'
// eastus is offer-restricted for Postgres Flexible Server on personal/trial
// subscriptions. eastus2 is the standard fallback that doesn't require a
// quota request. If you hit similar restrictions in eastus2, try centralus.
param location = 'eastus2'
param idpTenantId = 'f64ec93a-c5a6-4ba3-afca-8b10d684f3c1'
// acrName, storageAccountName, pgServerName intentionally unset — they default
// to subscription-scoped hashes in main.bicep. Override only if a specific
// name is required for institutional reasons.
param pgAdminPassword = readEnvironmentVariable('RAC_PG_ADMIN_PASSWORD')
param appGwTlsCertKvSecretId = readEnvironmentVariable('RAC_APPGW_TLS_CERT_KV_SECRET_ID')
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
