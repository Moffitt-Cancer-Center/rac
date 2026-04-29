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
// Phase 5 bridge: grant CP MI DNS Zone Contributor so it can create
// per-app records under the parent zone when provisioning Tier 3 apps.
// Principal ID belongs to id-rac-controlplane-dev (current dev sub).
param controlPlaneIdentityPrincipalId = '6e080a59-34ab-445d-9d14-9d51cf2d2138'
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

// ===== Phase 6: Shim =====
// Operator must seed `shim-database-dsn` and `shim-cookie-hmac` in the
// platform KV before deploying with shimImageName set.
param shimImageName = 'racdevacrczo2xbgcnq.azurecr.io/rac-shim:dev-003'
param shimIssuer = 'https://rac-dev.rac.checkwithscience.com'
param shimCookieDomain = '.rac-dev.rac.checkwithscience.com'
param shimInstitutionName = 'RAC Demo (checkwithscience.com)'
param shimMetricsEnabled = false

// ===== Phase 2: Control Plane =====
// Set deployControlPlaneApp=true once the image has been pushed to ACR and
// the operator has seeded `rac-pg-admin-password` in the platform KV.
param controlPlaneImageName = 'racdevacrczo2xbgcnq.azurecr.io/rac-control-plane:dev-003'
param deployControlPlaneApp = true
param controlPlaneIdpClientId = '3d0fb935-d02b-430a-9561-adb20633fbd4'
param controlPlaneIdpApiClientId = 'ac3d112d-fde7-4f37-812f-b911743698af'
param controlPlaneInstitutionName = 'RAC Demo (checkwithscience.com)'
param controlPlaneScanSeverityGate = 'high'
param controlPlaneApproverRoleResearch = 'rac-approver-research'
param controlPlaneApproverRoleIt = 'rac-approver-it'
// GitHub owner of the rac-pipeline repo. Drives both:
//   (a) the CP's RAC_GH_PIPELINE_OWNER env var (repository_dispatch URL), and
//   (b) the FIC subject 'repo:${owner}/${repo}:environment:${env}'.
// Defaults to '' in main.bicep — must be set per env or the FIC subject
// renders as 'repo:/rac-pipeline:environment:dev' which won't match any
// real GHA OIDC token claim.
param controlPlaneGithubPipelineOwner = 'jdkruzr'

// ===== Phase 1+2: Pipeline Trust (default-off; populated after manual app reg) =====
// Two-pass deploy: pass 1 leaves deployPipelineIdentity=false; operator manually
// creates rac-pipeline-dev app reg + GH Environment per docs/runbooks/bootstrap.md
// "Pipeline Trust Setup", populates the three IDs below, then re-deploys with
// deployPipelineIdentity=true and deployPipelineKv=true.
// deployPipelineKv can flip to true on Pass 1 if you want the CP to start minting
// callback secrets before the FIC+RBAC are wired (recommended for testing dispatch
// flow without a live pipeline run).
param deployPipelineKv = true
param deployPipelineIdentity = true
param pipelineAppUniqueNameDev = 'rac-pipeline-dev'
param pipelineAppPrincipalIdDev = '06855294-f2d9-4d1b-aaee-3e6ca80e10ba'
param pipelineAppClientIdDev = 'b9a23e53-9f35-44b6-94fc-763f1a1bf834'

// ===== Front Door custom domain (CustomerCertificate from bootstrap KV) =====
// Two-pass: pass 1 leaves deployCustomDomain=false so the FD profile picks up
// a system-assigned MI and the bootstrap-KV Secrets User role assignment runs
// + propagates; pass 2 flips this to true to create the FD secret and
// wildcard/apex customDomains. Cert is rac-dev-tls in kv-rac-bootstrap-001
// (LE wildcard issued via DNS-01). Default false so a fresh-from-teardown
// deploy follows the documented two-pass; set to true on the second deploy.
param deployCustomDomain = false
param tlsCertName = 'rac-dev-tls'
