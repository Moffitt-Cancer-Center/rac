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

// pg_uuidv7 is NOT in the azure.extensions allowlist for eastus2 (varies by
// region + PG version). Fall back to uuid-ossp, which is always available.
// Migration code that uses uuid_generate_v7() must be updated to
// uuid_generate_v4() — see docs/runbooks/bootstrap.md section 8.
param pgExtensions = ['uuid-ossp']
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

// ===== Phase 2: Control Plane =====
// Set deployControlPlaneApp=true once the image has been pushed to ACR and
// the operator has seeded `rac-pg-admin-password` in the platform KV.
param controlPlaneImageName = 'racdevacrczo2xbgcnq.azurecr.io/rac-control-plane:dev-001'
param deployControlPlaneApp = true
param controlPlaneIdpClientId = '3d0fb935-d02b-430a-9561-adb20633fbd4'
param controlPlaneIdpApiClientId = 'ac3d112d-fde7-4f37-812f-b911743698af'
param controlPlaneInstitutionName = 'RAC Demo (checkwithscience.com)'
param controlPlaneScanSeverityGate = 'high'
param controlPlaneApproverRoleResearch = 'rac-approver-research'
param controlPlaneApproverRoleIt = 'rac-approver-it'
