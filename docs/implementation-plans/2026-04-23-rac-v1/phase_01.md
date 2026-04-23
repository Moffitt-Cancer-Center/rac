# Phase 1: Repo scaffold and Tier 2 infrastructure foundation

**Goal:** Provision the full static Tier 2 Azure platform (networking, ACA environment, ACR, Postgres, Key Vault, Blob, Log Analytics/App Insights, Front Door, App Gateway, DNS zone) via Bicep IaC, with a GHA deploy workflow that is human-gated dev → staging → prod. No application code yet.

**Architecture:** Infrastructure-only phase. Bicep modules composed by a single `main.bicep` at subscription scope. Per-environment parameters live in `infra/environments/{dev,staging,prod}.bicepparam`. GHA uses OIDC federated identity (no service principal secrets). Tagging convention is centralized in a shared `modules/tags.bicep` fragment and applied at resource creation. Authorization model is RBAC throughout (no Key Vault access policies, no SAS-only patterns). Two resource groups per environment: `rg-rac-<env>` (Tier 2 platform) and `rg-rac-tier3-<env>` (Tier 3 dynamic researcher apps — populated by Control Plane via Azure SDK in Phase 5, but the empty RG is pre-created here with correct tags).

**Tech Stack:** Bicep (Microsoft.App, Microsoft.ContainerRegistry, Microsoft.DBforPostgreSQL, Microsoft.KeyVault, Microsoft.Storage, Microsoft.OperationalInsights, Microsoft.Insights, Microsoft.Cdn/Front Door, Microsoft.Network, Microsoft.Network/dnsZones), GitHub Actions with `azure/login@v2` OIDC federation, Azure CLI `az deployment sub create`/`what-if`.

**Scope:** Phase 1 of 8 from the original design.

**Codebase verified:** 2026-04-23 — confirmed greenfield. Only `/home/sysop/rac/docs/` exists (containing only the design plan). No `apps/`, `infra/`, or `.github/` yet. Repo is a git repo on `main`, single commit, no remote configured.

---

## Acceptance Criteria Coverage

This phase implements and tests (operationally):

### rac-v1.AC1: Platform is deployable from source
- **rac-v1.AC1.1 Success:** `infra-deploy.yml` against a clean dev Azure subscription provisions all Tier 2 resources without manual intervention.
- **rac-v1.AC1.2 Success:** `az deployment sub what-if` on `main.bicep` against an unchanged environment produces zero changes.
- **rac-v1.AC1.3 Success:** Promotion workflow requires human approval before deploying to staging or prod.
- **rac-v1.AC1.4 Failure:** Missing a required deployment parameter (e.g., `PARENT_DOMAIN`) causes deploy to fail with a clear, actionable error message naming the missing parameter.
- **rac-v1.AC1.5 Edge:** All provisioned resources carry the required tags (`rac_env` at minimum) at the moment of creation — not as a follow-up apply.

### rac-v1.AC11: Cost attribution works (partial — Tier 2 portion only)
- **rac-v1.AC11.1 Success (Tier 2 portion):** Every Tier 2 Azure resource carries `rac_env` (and placeholder `rac_app_slug=null`, `rac_pi_principal_id=null`, `rac_submission_id=null` as applicable — Tier 2 resources are not tied to a specific app) at creation time. Tier 3 tagging is completed in Phase 5.

### rac-v1.AC10: Observability is operational (infra portions)
- **rac-v1.AC10.3 Success:** The pager-tier alert for shim 5xx rate > 1% over 5 minutes fires in response to a controlled fault injection and pages the on-call channel. Additional alerts: Control Plane 5xx > 1% over 5 min; Postgres connection failures; Key Vault access denied; pipeline workflow stuck > 2× timeout.
- **rac-v1.AC10.5 Edge:** The Event Hub export surface exists per `docs/runbooks/siem-export.md` and can be subscribed to by a test consumer without any code changes.

**Verifies:** Operational (no unit tests). Verification commands appear under each task.

---

## File Classification Policy

Bicep files (`.bicep`, `.bicepparam`) are declarative IaC — exempt from FCIS classification. GitHub Actions workflow YAML is also exempt. Markdown runbooks are exempt. No files in this phase contain runtime Python/TypeScript code.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Repo hygiene scaffold (.gitignore, README.md, CODEOWNERS)

**Verifies:** None (infrastructure setup)

**Files:**
- Create: `/home/sysop/rac/.gitignore`
- Create: `/home/sysop/rac/README.md`
- Create: `/home/sysop/rac/CODEOWNERS`

**Implementation:**

`.gitignore` should exclude: Python artifacts (`__pycache__/`, `*.pyc`, `*.egg-info/`, `.venv/`, `.pytest_cache/`, `htmlcov/`, `.coverage`), Node artifacts (`node_modules/`, `dist/`, `.vite/`), Bicep artifacts (`*.bicep.deployment.json`), IDE dirs (`.idea/`, `.vscode/`), environment files (`.env`, `.env.*` except `.env.example`), and scratch dirs (`/tmp/`, `.worktrees/`).

`README.md` should contain: project name (RAC — Research Application Commons), one-paragraph summary (lift from design plan Summary), link to `docs/design-plans/2026-04-23-rac-v1.md`, link to `docs/runbooks/bootstrap.md`, dev environment prerequisites (Azure CLI ≥ 2.86, Bicep CLI ≥ 0.31, Python 3.12, Node 20, pnpm ≥ 9), and top-level directory map (`apps/`, `infra/`, `docs/`, `.github/`).

`CODEOWNERS` assigns `@jarett` (placeholder — the task executor should confirm the user's GitHub handle with the operator before committing) to the entire repo. Format: `* @jarett`.

**Verification:**
```bash
cd /home/sysop/rac
test -f .gitignore && test -f README.md && test -f CODEOWNERS
git check-ignore .venv node_modules  # both must be ignored
```

**Commit:** `chore: repo hygiene scaffold`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Shared tags helper (modules/tags.bicep)

**Verifies:** `rac-v1.AC1.5`, `rac-v1.AC11.1` (Tier 2 portion)

**Files:**
- Create: `/home/sysop/rac/infra/modules/tags.bicep`

**Implementation:**

A reusable Bicep user-defined-function module exporting a `buildTags` function (or alternatively exposing an `output tags object`). Bicep user-defined functions (`func`) are stable as of Bicep CLI 0.26.0+. Use this form:

```bicep
@export()
func buildTags(racEnv string, extra object) object => union(
  {
    rac_env: racEnv
    rac_managed_by: 'bicep'
  },
  extra
)
```

All downstream modules import this via `import { buildTags } from '../modules/tags.bicep'` and pass the result to each resource's `tags:` property at creation. Do NOT use a separate `Microsoft.Resources/tags` post-apply step — tags must be present at creation (AC1.5).

Callers in `main.bicep` compose per-deployment tags once (e.g., `var commonTags = buildTags(racEnv, {})`) and thread them through every module as a `tags object` parameter.

**Verification:**
```bash
cd /home/sysop/rac/infra
az bicep build --file modules/tags.bicep  # compiles without errors
```

**Commit:** `feat(infra): shared tags helper`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-5) -->

<!-- START_TASK_3 -->
### Task 3: Network module (modules/network.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/network.bicep`

**Implementation:**

One VNet with address space `10.${vnetOctet}.0.0/16` parameterized by `vnetOctet` (default 10 for dev, 20 for staging, 30 for prod). Subnets:

- `snet-aca` (`10.X.0.0/21`, `/21` required by ACA managed environment, delegated to `Microsoft.App/environments`)
- `snet-appgw` (`10.X.8.0/24`, no delegation)
- `snet-pe` (`10.X.9.0/24`, private endpoints, `privateEndpointNetworkPolicies: 'Disabled'`)
- `snet-pg` (`10.X.10.0/24`, reserved for Postgres private endpoint)

Parameters: `location string`, `racEnv string`, `vnetOctet int`, `tags object`.
Outputs: `vnetId`, `acaSubnetId`, `appGwSubnetId`, `peSubnetId`, `pgSubnetId`.

Use `Microsoft.Network/virtualNetworks@2024-05-01` with subnets declared inline on the VNet (not as separate child resources — avoids subnet drift).

Apply `tags: tags` at resource creation.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/network.bicep
```

**Commit:** `feat(infra): network module (VNet + subnets)`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Log Analytics + App Insights module (modules/log-analytics.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/log-analytics.bicep`

**Implementation:**

Creates `Microsoft.OperationalInsights/workspaces@2023-09-01` (PerGB2018 SKU, 30-day retention default, overrideable per env) and a workspace-based `Microsoft.Insights/components@2020-02-02` with `Application_Type: 'web'`, `WorkspaceResourceId` pointing at the workspace.

Parameters: `location`, `racEnv`, `workspaceName`, `componentName`, `retentionDays int = 30`, `tags object`.
Outputs: `workspaceId`, `workspaceCustomerId`, `appInsightsConnectionString`, `appInsightsId`.

Tag both resources with `tags`. Do NOT use the deprecated classic App Insights resource type.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/log-analytics.bicep
```

**Commit:** `feat(infra): log analytics + app insights module`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Azure Container Registry module (modules/acr.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/acr.bicep`

**Implementation:**

`Microsoft.ContainerRegistry/registries@2023-11-01-preview`, Premium SKU (required for Defender for Containers). Properties:
- `adminUserEnabled: false`
- `publicNetworkAccess: 'Disabled'` (access via private endpoint only)
- `policies.quarantinePolicy.status: 'enabled'` (images are quarantined until Defender scan completes)

Also create a `Microsoft.Network/privateEndpoints@2023-11-01` resource targeting the ACR's `registry` subresource, placed in `snet-pe`.

Parameters: `location`, `racEnv`, `acrName` (must be globally unique, max 50 chars, alphanumeric), `peSubnetId`, `tags object`.
Outputs: `acrId`, `acrLoginServer`, `acrResourceId`.

Apply `tags` on both the registry and the private endpoint.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/acr.bicep
```

**Commit:** `feat(infra): ACR module with private endpoint`
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 6-9) -->

<!-- START_TASK_6 -->
### Task 6: Key Vault module (modules/key-vault.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/key-vault.bicep`

**Implementation:**

`Microsoft.KeyVault/vaults@2023-07-01` with:
- `sku.name: 'standard'` (Premium only for HSM; not needed in v1)
- `enableRbacAuthorization: true`
- `enableSoftDelete: true`, `softDeleteRetentionInDays: 90`
- `enablePurgeProtection: true`
- `publicNetworkAccess: 'Disabled'`
- `networkAcls.defaultAction: 'Deny'`, `networkAcls.bypass: 'AzureServices'`

Private endpoint for the `vault` subresource, placed in `snet-pe`.

Parameters: `location`, `racEnv`, `kvName`, `tenantId`, `peSubnetId`, `tags object`.
Outputs: `kvId`, `kvUri`, `kvName`.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/key-vault.bicep
```

**Commit:** `feat(infra): key vault module`
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Blob Storage module (modules/blob-storage.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/blob-storage.bicep`

**Implementation:**

`Microsoft.Storage/storageAccounts@2023-05-01` with:
- `sku.name: 'Standard_GRS'` (geo-redundant for production; `Standard_LRS` for dev)
- `kind: 'StorageV2'`
- `properties.minimumTlsVersion: 'TLS1_2'`
- `properties.allowBlobPublicAccess: false`
- `properties.publicNetworkAccess: 'Disabled'`
- `properties.networkAcls.defaultAction: 'Deny'`, `bypass: 'AzureServices'`

Blob containers (as `Microsoft.Storage/storageAccounts/blobServices/containers` child resources):
- `researcher-uploads`
- `scan-artifacts`
- `sboms`
- `cost-exports`
- `build-logs`

Lifecycle policy via `Microsoft.Storage/storageAccounts/managementPolicies`:
- `scan-artifacts`, `sboms`, `build-logs`: move to Cool after 60 days, Archive after 365 days
- `cost-exports`: delete after 730 days
- `researcher-uploads`: no lifecycle movement (assets must stay warm; retained with submission)

Private endpoint for the `blob` subresource in `snet-pe`.

Parameters: `location`, `racEnv`, `storageAccountName` (globally unique, 3-24 lowercase alphanumeric), `peSubnetId`, `sku string = 'Standard_GRS'`, `tags object`.
Outputs: `storageAccountId`, `storageAccountName`, `blobEndpoint`.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/blob-storage.bicep
```

**Commit:** `feat(infra): blob storage module`
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Postgres Flexible Server module (modules/postgres.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/postgres.bicep`

**Implementation:**

`Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview`:
- `properties.version: '16'` (Postgres 16; design assumes UUIDv7 available via `pg_uuidv7` extension, which is enabled in a post-deploy task in Phase 2)
- `sku.name`: `Standard_B2s` (dev), `Standard_D2s_v3` (staging/prod) — parameterized
- `sku.tier`: `'Burstable'` (dev), `'GeneralPurpose'` (staging/prod)
- `properties.storage.storageSizeGB: 32` (dev), `128` (prod)
- `properties.highAvailability.mode: 'Disabled'` (dev), `'ZoneRedundant'` (prod)
- `properties.backup.backupRetentionDays: 7` (dev), `35` (prod)
- `properties.administratorLogin: 'rac_admin'`
- `properties.administratorLoginPassword: @secure()` — passed from Key Vault reference in bicepparam, NOT stored in source

Networking: Private access via a private endpoint on `snet-pg` (delegated-subnet VNet integration is not supported on Burstable; private endpoint is the universal pattern).

Also create a `Microsoft.DBforPostgreSQL/flexibleServers/configurations` child resource to enable `pg_uuidv7` in `azure.extensions` shared_preload_libraries. The extension is allowlisted in Azure Postgres; actual `CREATE EXTENSION` runs in Phase 2 migrations.

Parameters: `location`, `racEnv`, `serverName`, `adminPassword @secure()`, `skuName`, `skuTier`, `storageSizeGB int`, `haMode string`, `backupRetentionDays int`, `pgSubnetId`, `tags object`.
Outputs: `serverId`, `serverFqdn`.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/postgres.bicep
```

**Commit:** `feat(infra): postgres flexible server module`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: ACA Managed Environment module (modules/aca-env.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/aca-env.bicep`

**Implementation:**

`Microsoft.App/managedEnvironments@2024-03-01`:
- `properties.appLogsConfiguration.destination: 'log-analytics'`
- `properties.appLogsConfiguration.logAnalyticsConfiguration.customerId: workspaceCustomerId` (from Log Analytics module output)
- `properties.appLogsConfiguration.logAnalyticsConfiguration.sharedKey` — fetched via `listKeys()` expression referencing the workspace resource
- `properties.vnetConfiguration.infrastructureSubnetId: acaSubnetId`
- `properties.vnetConfiguration.internal: true` (internal ingress by default; public traffic arrives via App Gateway)
- `properties.zoneRedundant: true` (prod only; parameterized)
- `properties.workloadProfiles`: one `Consumption` profile named `Consumption`; for prod, add a `D4` dedicated profile named `apps`

Parameters: `location`, `racEnv`, `envName`, `acaSubnetId`, `workspaceCustomerId`, `workspaceId`, `zoneRedundant bool`, `profileSku string`, `tags object`.
Outputs: `envId`, `envDefaultDomain` (e.g., `<env>.internal.<region>.azurecontainerapps.io`), `envStaticIp`.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/aca-env.bicep
```

**Commit:** `feat(infra): ACA managed environment module`
<!-- END_TASK_9 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_SUBCOMPONENT_D (tasks 10-12) -->

<!-- START_TASK_10 -->
### Task 10: DNS Zone module (modules/dns-zone.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/dns-zone.bicep`

**Implementation:**

`Microsoft.Network/dnsZones@2018-05-01` for `${PARENT_DOMAIN}`. This is a public zone — parent-zone delegation (NS records pointing here from the parent) is a Tier 1 manual step documented in `docs/runbooks/bootstrap.md` (Task 17). Bicep creates the zone; the operator adds parent NS records manually.

Also create a role assignment granting the Control Plane's eventual managed identity the `DNS Zone Contributor` role on this zone (role definition ID `befefa01-2a29-4197-83a8-272ff33ce314`). The Control Plane's managed identity principal ID is a Bicep parameter (`controlPlaneIdentityPrincipalId string`, default empty) — role assignment is created only when the parameter is non-empty. Phase 5 supplies the principal ID once the Control Plane's identity exists; Phase 1 leaves it empty and the assignment is skipped. Use a conditional resource (`if (!empty(controlPlaneIdentityPrincipalId))`).

Parameters: `parentDomain string`, `racEnv string`, `controlPlaneIdentityPrincipalId string = ''`, `tags object`.
Outputs: `zoneId`, `zoneNameServers array`.

Zone tagging applies at creation.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/dns-zone.bicep
```

**Commit:** `feat(infra): DNS zone module`
<!-- END_TASK_10 -->

<!-- START_TASK_11 -->
### Task 11: Azure Front Door Premium + WAF module (modules/front-door.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/front-door.bicep`

**Implementation:**

Resources:
- `Microsoft.Cdn/profiles@2023-05-01` (Premium_AzureFrontDoor SKU)
- `Microsoft.Cdn/profiles/afdEndpoints@2023-05-01` (endpoint under the profile)
- `Microsoft.Cdn/profiles/originGroups@2023-05-01` (single group pointing at the App Gateway public IP)
- `Microsoft.Cdn/profiles/originGroups/origins@2023-05-01` (origin referencing App Gateway public FQDN; `privateLinkId` points at the App Gateway if Private Link origin is used)
- `Microsoft.Cdn/profiles/afdEndpoints/routes@2023-05-01` (wildcard route for `*.${parentDomain}` → origin group)
- `Microsoft.Cdn/profiles/customDomains@2023-05-01` (custom domain `*.${parentDomain}` with `ManagedCertificate` TLS)
- `Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2022-05-01` with `policySettings.mode: 'Prevention'`, managed ruleset `Microsoft_DefaultRuleSet_2.1`
- `Microsoft.Cdn/profiles/securityPolicies@2023-05-01` associating the WAF policy with the endpoint

App Gateway public IP/FQDN is an input to this module (wired in `main.bicep`).

Parameters: `racEnv`, `parentDomain string`, `appGatewayPublicFqdn string`, `appGatewayPrivateLinkResourceId string = ''` (if empty, use public FQDN origin; else Private Link), `tags object`.
Outputs: `frontDoorProfileId`, `frontDoorEndpointHostname`, `wafPolicyId`.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/front-door.bicep
```

**Commit:** `feat(infra): Front Door Premium + WAF module`
<!-- END_TASK_11 -->

<!-- START_TASK_12 -->
### Task 12: Application Gateway v2 + WAF module (modules/app-gateway.bicep)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/app-gateway.bicep`

**Implementation:**

`Microsoft.Network/applicationGateways@2023-11-01`:
- `sku.name: 'WAF_v2'`, `sku.tier: 'WAF_v2'`, `sku.capacity: 2`
- `properties.gatewayIPConfigurations`: points at `snet-appgw`
- `properties.frontendIPConfigurations`: one public IP (separate `Microsoft.Network/publicIPAddresses@2023-11-01`, Standard SKU, Static, zone-redundant)
- `properties.frontendPorts`: 443
- `properties.backendAddressPools`: one pool with FQDN pointing at the ACA environment's default domain (resolves internally to Shim once Phase 6 deploys it)
- `properties.backendHttpSettingsCollection`: one setting with `pickHostNameFromBackendAddress: true`, `port: 443`, `protocol: 'Https'`, `cookieBasedAffinity: 'Disabled'`, `requestTimeout: 120`
- `properties.httpListeners`: one HTTPS listener with multi-site hostname `*.${parentDomain}`; TLS certificate provisioned via Key Vault reference (certificate secret ref parameterized; actual cert provisioning is Tier 1 manual in v1 — documented in bootstrap runbook)
- `properties.requestRoutingRules`: basic rule: listener → backend pool → backend settings
- `properties.webApplicationFirewallConfiguration`: enabled, `firewallMode: 'Prevention'`, OWASP 3.2 ruleset, or use the newer `firewallPolicy` sub-resource pointing at a `Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies` resource (preferred — use this form)

Also create `Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2023-11-01` with Prevention mode and Managed Rules `OWASP 3.2`.

Parameters: `location`, `racEnv`, `appGwName`, `appGwSubnetId`, `parentDomain string`, `tlsCertKvSecretId string`, `tags object`.
Outputs: `appGatewayId`, `appGatewayPublicFqdn`, `appGatewayPublicIp`.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/app-gateway.bicep
```

**Commit:** `feat(infra): App Gateway v2 + WAF module`
<!-- END_TASK_12 -->

<!-- END_SUBCOMPONENT_D -->

<!-- START_SUBCOMPONENT_E (tasks 13-14) -->

<!-- START_TASK_12B -->
### Task 12B: Managed identity module (modules/managed-identity.bicep)

**Verifies:** Foundation for AC6, AC9, AC11.1 (Tier 3) — used by Phase 5.

**Files:**
- Create: `/home/sysop/rac/infra/modules/managed-identity.bicep`

**Implementation:**

Creates user-assigned managed identities for the Control Plane and the Shim. Each `Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview`:
- `id-rac-controlplane-<env>` — used by the Control Plane ACA app (Phase 2) + Tier 3 provisioning (Phase 5) + Graph sweep job (Phase 5). Role assignments for this MI are added progressively: `DNS Zone Contributor` on the DNS child zone (added here conditionally by Task 10 when `controlPlaneIdentityPrincipalId` parameter is populated on the second Phase 1 apply — see re-deploy loop note below), `Key Vault Crypto Officer` on the platform Key Vault, `Storage Account Key Operator Service Role` + `Contributor` on the Tier-3 storage account, `Contributor` on the Tier 3 resource group.
- `id-rac-shim-<env>` — used by the Shim ACA app (Phase 6). Roles: `Key Vault Crypto User` on the platform Key Vault (for public-key reads only).

Both MIs carry the same tag convention (`rac_env`).

Parameters: `location`, `racEnv`, `tags object`, `kvResourceId string`, `tier3StorageAccountId string`, `tier3ResourceGroupId string`, `dnsZoneId string`.
Outputs: `controlPlaneMiResourceId`, `controlPlaneMiPrincipalId`, `controlPlaneMiClientId`, `shimMiResourceId`, `shimMiPrincipalId`, `shimMiClientId`.

All RBAC role assignments are declared in this module scoped to the target resources, conditional on the MIs being created (avoids circular dependencies with modules that read these outputs).

**Re-deploy loop (operational note):** On the very first deploy, the DNS zone (Task 10) does not assign the `DNS Zone Contributor` role because the MI does not yet exist. Task 10 handles this with `if (!empty(controlPlaneIdentityPrincipalId))`. After Phase 1 runs end-to-end once creating the MIs, the operator runs `infra-deploy` a second time with `controlPlaneIdentityPrincipalId` set to the Control Plane MI's principal ID (captured as a `main.bicep` output on the first run). The bootstrap runbook documents this two-pass startup. Subsequent updates are idempotent.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/managed-identity.bicep
```

**Commit:** `feat(infra): managed identity module + Control Plane/Shim identities`
<!-- END_TASK_12B -->

<!-- START_TASK_12C -->
### Task 12C: Alerts + Action Group module (modules/alerts.bicep)

**Verifies:** `rac-v1.AC10.3`

**Files:**
- Create: `/home/sysop/rac/infra/modules/alerts.bicep`

**Implementation:**

Resources:
- `Microsoft.Insights/actionGroups@2023-01-01`: one action group per env, `groupShortName: 'rac-${racEnv}'`. Receivers: Email (institution oncall), Webhook (ServiceNow/PagerDuty via Logic Apps or direct — operator wires per-deployment; module takes a `actionGroupWebhookUri` param and optional email list). Keep it generic; pager-vendor-specific wiring is operator-configured.
- `Microsoft.Insights/metricAlerts@2018-03-01` × 5:
  - `alert-shim-5xx`: target = Shim ACA app (resource ID passed as parameter but empty on first apply; alert conditionally created). Metric `Requests` filtered on `statusCodeCategory='5xx'` / total `Requests` > 0.01 over 5 min. Severity 1.
  - `alert-controlplane-5xx`: same pattern, target = Control Plane ACA app.
  - `alert-postgres-connection-failures`: target = Postgres Flexible Server, metric `connections_failed` > 0 over 5 min. Severity 1.
  - `alert-keyvault-access-denied`: target = Key Vault, metric `ServiceApiResult` filtered on `ResultType='Forbidden'` count > 0 over 5 min. Severity 1.
  - `alert-pipeline-stuck`: a Log Analytics `scheduledQueryRules` (kusto-based) watching the `access_log`-adjacent custom table for pipeline events; fires when no terminal verdict callback is observed within 2× the configured pipeline timeout. Severity 2.

Parameters: `location`, `racEnv`, `actionGroupEmails array`, `actionGroupWebhookUri string = ''`, `shimAppId string = ''`, `controlPlaneAppId string = ''`, `postgresServerId`, `kvId`, `logAnalyticsWorkspaceId`, `pipelineTimeoutMinutes int = 120`, `tags object`.

Conditionally skip alerts whose target app IDs are empty (first deploy pass where Shim + Control Plane ACA apps don't exist yet). Re-run Phase 1 after Phase 2 deploys the Control Plane and Phase 6 deploys the Shim to activate those alerts — same re-deploy loop pattern as the MI.

**Controlled fault injection (acceptance for AC10.3):** documented in `docs/runbooks/incident-response.md` (Task 16): a test script posts 100 requests to an endpoint known to return 503 on the dev shim, waits 5 min, confirms action group receives the alert (email or webhook receipt visible in action group history). Scripted verification added to `phase1-acceptance-report.md` in Task 17.

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/alerts.bicep
```

**Commit:** `feat(infra): alerts + action group module (AC10.3)`
<!-- END_TASK_12C -->

<!-- START_TASK_12D -->
### Task 12D: Event Hub + diagnostic settings (modules/event-hub.bicep)

**Verifies:** `rac-v1.AC10.5`

**Files:**
- Create: `/home/sysop/rac/infra/modules/event-hub.bicep`

**Implementation:**

Resources:
- `Microsoft.EventHub/namespaces@2024-01-01` (Standard SKU, single throughput unit, zone-redundant in prod, no auto-inflate initially).
- `Microsoft.EventHub/namespaces/eventhubs@2024-01-01` × 2:
  - `eh-rac-access-logs` — target for Shim `access_log`-adjacent diagnostic settings.
  - `eh-rac-approval-events` — target for Control Plane `approval_event`-adjacent diagnostic settings.
- `Microsoft.EventHub/namespaces/authorizationRules@2024-01-01` for a `Listen`-only consumer group credential that SIEM subscribers (operator-configured outside the platform) use.
- `Microsoft.Insights/diagnosticSettings` on the platform Log Analytics workspace, forwarding the relevant tables (`RAC_AccessLog_CL`, `RAC_ApprovalEvent_CL`) to the respective Event Hubs.

The Control Plane and Shim write their append-only tables both to Postgres (source of truth) and to Log Analytics custom tables (structured JSON via `structlog` Azure handler from Phase 2 Task 2). Log Analytics diagnostic settings forward to Event Hub. No application code changes required for SIEM consumers — hence "subscribable without code changes" (AC10.5).

Parameters: `location`, `racEnv`, `logAnalyticsWorkspaceId`, `tags object`.
Outputs: `eventHubNamespaceId`, `accessLogsEventHubId`, `approvalEventsEventHubId`, `listenerConnectionStringSecretRef` (stored in platform Key Vault).

**Verification:**
```bash
az bicep build --file /home/sysop/rac/infra/modules/event-hub.bicep
```

**Commit:** `feat(infra): Event Hub + SIEM diagnostic settings (AC10.5)`
<!-- END_TASK_12D -->

<!-- START_TASK_13 -->
### Task 13: main.bicep top-level composition

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.4`, `rac-v1.AC1.5`, `rac-v1.AC10.3`, `rac-v1.AC10.5`

**Files:**
- Create: `/home/sysop/rac/infra/main.bicep`

**Implementation:**

`targetScope = 'subscription'`. At the top of the file declare required parameters with explicit types and `@description` annotations — no defaults for deployment-critical values (missing-parameter error surfaces the parameter name per AC1.4):

```bicep
targetScope = 'subscription'

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
param appGwTlsCertKvSecretId string

@description('Control Plane managed identity principal ID (empty on first deploy; populated in Phase 5 Task 1 re-deploy)')
param controlPlaneIdentityPrincipalId string = ''

@description('VNet third octet (10/20/30 for dev/staging/prod)')
param vnetOctet int

@description('Postgres sizing')
param pgSkuName string
param pgSkuTier string
param pgStorageSizeGB int
param pgHaMode string
param pgBackupRetentionDays int

@description('ACA zone redundancy')
param acaZoneRedundant bool
param acaProfileSku string

@description('Alert action group — email recipients (comma-separated oncall addresses)')
param actionGroupEmails array = []

@description('Alert action group — optional webhook URI (PagerDuty/ServiceNow). Leave empty to skip.')
param actionGroupWebhookUri string = ''

@description('ACA app resource IDs for alerts (empty on first deploy; populate after Phase 2/6)')
param controlPlaneAppId string = ''
param shimAppId string = ''

@description('Pipeline timeout in minutes (used to compute stuck-pipeline alert threshold)')
param pipelineTimeoutMinutes int = 120
```

Missing any required parameter at deploy time produces an Azure Resource Manager error that names the specific parameter — native ARM behavior; no custom validation needed (satisfies AC1.4).

Body:
1. `import { buildTags } from 'modules/tags.bicep'`; `var commonTags = buildTags(racEnv, {})`
2. Create **two** resource groups:
   ```bicep
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
   ```
   `rg-rac-tier3-<env>` is pre-created empty here. The Control Plane populates it with researcher ACA apps in Phase 5. Creating it now ensures the Tier 3 RG exists and is tagged before any SDK calls.
3. Invoke infrastructure modules scoped to `rg` in dependency order: `network`, `logAnalytics`, `keyVault`, `blobStorage`, `postgres`, `acr`, `acaEnv`, `dnsZone`, `appGateway`, `frontDoor` — each passed `tags: commonTags`.
4. After core infrastructure modules, invoke the three new modules:
   - `managedIdentity` (module: `modules/managed-identity.bicep`) — pass `kvResourceId`, `tier3ResourceGroupId: rgTier3.id`, `dnsZoneId`, `controlPlaneIdentityPrincipalId`; scoped to `rg`.
   - `alerts` (module: `modules/alerts.bicep`) — pass `shimAppId`, `controlPlaneAppId`, `postgresServerId`, `kvId`, `logAnalyticsWorkspaceId`, `actionGroupEmails`, `actionGroupWebhookUri`, `pipelineTimeoutMinutes`; scoped to `rg`.
   - `eventHub` (module: `modules/event-hub.bicep`) — pass `logAnalyticsWorkspaceId`; scoped to `rg`.
5. Wire all module outputs into subsequent modules' inputs.

Outputs at subscription scope: `resourceGroupName`, `tier3ResourceGroupName`, `acrLoginServer`, `keyVaultUri`, `acaEnvDefaultDomain`, `appGatewayPublicFqdn`, `frontDoorEndpointHostname`, `dnsZoneNameServers`, `controlPlaneMiPrincipalId`, `shimMiPrincipalId`, `eventHubNamespaceId`.

**Verification:**
```bash
cd /home/sysop/rac/infra
az bicep build --file main.bicep  # must compile with zero warnings treated as errors
```

**Commit:** `feat(infra): main.bicep subscription-scoped composition (two RGs + MI + alerts + Event Hub)`
<!-- END_TASK_13 -->

<!-- START_TASK_14 -->
### Task 14: Per-environment parameter files (environments/{dev,staging,prod}.bicepparam)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.2`

**Files:**
- Create: `/home/sysop/rac/infra/environments/dev.bicepparam`
- Create: `/home/sysop/rac/infra/environments/staging.bicepparam`
- Create: `/home/sysop/rac/infra/environments/prod.bicepparam`
- Create: `/home/sysop/rac/infra/environments/README.md` (explains where to populate deployment-specific values)

**Implementation:**

Each `.bicepparam` starts with `using '../main.bicep'`. Set environment-specific values. `pgAdminPassword` is referenced via Key Vault, not inlined:

```bicep
using '../main.bicep'

param racEnv = 'dev'
param parentDomain = 'rac-dev.example.org'  // REPLACE at deployment time
param location = 'eastus'
param idpTenantId = '00000000-0000-0000-0000-000000000000'  // REPLACE
param acrName = 'racdevacr001'  // globally unique
param storageAccountName = 'racdevst001'  // globally unique
param pgServerName = 'rac-dev-pg'
param pgAdminPassword = getSecret('<subscription-id>', '<rg-name>', '<kv-name>', 'pg-admin-password-dev')
param appGwTlsCertKvSecretId = 'https://<bootstrap-kv>.vault.azure.net/secrets/appgw-cert-dev/<version>'
param controlPlaneIdentityPrincipalId = ''
param vnetOctet = 10
param pgSkuName = 'Standard_B2s'
param pgSkuTier = 'Burstable'
param pgStorageSizeGB = 32
param pgHaMode = 'Disabled'
param pgBackupRetentionDays = 7
param acaZoneRedundant = false
param acaProfileSku = 'Consumption'
```

`staging.bicepparam`: `racEnv='staging'`, `vnetOctet=20`, SKU `Standard_D2s_v3`, tier `GeneralPurpose`, `pgHaMode='SameZone'`, backup 14 days, `acaZoneRedundant=true`.

`prod.bicepparam`: `racEnv='prod'`, `vnetOctet=30`, larger Postgres, `pgHaMode='ZoneRedundant'`, backup 35 days, `acaProfileSku='D4'`.

`getSecret()` is the Bicep parameter-file-only function for fetching Key Vault secret values at deploy time without materializing the secret in source. It requires the deploying principal to have Key Vault access at deploy time. A Tier 1 bootstrap Key Vault (separate from the platform Key Vault provisioned here) holds the admin password; documented in the bootstrap runbook.

`README.md` documents every placeholder and where to obtain the values.

**Verification:**
```bash
cd /home/sysop/rac/infra
az bicep build-params --file environments/dev.bicepparam  # validates .bicepparam compiles
az bicep build-params --file environments/staging.bicepparam
az bicep build-params --file environments/prod.bicepparam
```

**Commit:** `feat(infra): per-env bicepparam files`
<!-- END_TASK_14 -->

<!-- END_SUBCOMPONENT_E -->

<!-- START_SUBCOMPONENT_F (tasks 15-16) -->

<!-- START_TASK_15 -->
### Task 15: GitHub Actions infra-deploy workflow

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.2`, `rac-v1.AC1.3`, `rac-v1.AC1.4`

**Files:**
- Create: `/home/sysop/rac/.github/workflows/infra-deploy.yml`

**Implementation:**

Workflow with three jobs, chained with `needs:`. Shared steps factored into a composite action if duplication becomes painful; otherwise inline.

```yaml
name: infra-deploy

on:
  push:
    branches: [main]
    paths: ['infra/**']
  workflow_dispatch:
    inputs:
      environment:
        type: choice
        options: [dev, staging, prod]
        default: dev

permissions:
  id-token: write
  contents: read

jobs:
  whatif-dev:
    runs-on: ubuntu-latest
    environment: dev
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID_DEV }}
      - name: What-if
        run: |
          az deployment sub what-if \
            --location ${{ vars.AZURE_LOCATION }} \
            --template-file infra/main.bicep \
            --parameters infra/environments/dev.bicepparam

  deploy-dev:
    needs: whatif-dev
    runs-on: ubuntu-latest
    environment: dev  # requires GH Environment approval if configured
    steps:
      # same azure/login
      - name: Deploy
        run: |
          az deployment sub create \
            --name rac-dev-${{ github.run_id }} \
            --location ${{ vars.AZURE_LOCATION }} \
            --template-file infra/main.bicep \
            --parameters infra/environments/dev.bicepparam

  deploy-staging:
    needs: deploy-dev
    runs-on: ubuntu-latest
    environment: staging  # GH Environment protection: required reviewer (configured in repo settings)
    # ...same pattern, staging.bicepparam, AZURE_SUBSCRIPTION_ID_STAGING

  deploy-prod:
    needs: deploy-staging
    runs-on: ubuntu-latest
    environment: prod  # GH Environment protection: required reviewer
    # ...same pattern, prod.bicepparam, AZURE_SUBSCRIPTION_ID_PROD
```

The human-approval gate (AC1.3) is enforced by GitHub Environment protection rules — `staging` and `prod` environments must be configured in repo Settings → Environments with required reviewers. The workflow YAML references the environment; the gate itself is a GitHub configuration step, not inline YAML.

Document the required GH Environment setup steps in the workflow file header comment: which environments to create, which secrets/vars each needs (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID_<ENV>`, var `AZURE_LOCATION`), which reviewer teams to require on staging/prod.

**Verification:**
```bash
# Lint
npx --yes action-validator /home/sysop/rac/.github/workflows/infra-deploy.yml
# Push to a branch, open PR, confirm workflow shows up. Full acceptance is AC1.1/AC1.3 runtime verification on real Azure — done during Tier 1 operator handoff per bootstrap runbook.
```

**Commit:** `feat(infra): GHA deploy workflow with human-gated promotion`
<!-- END_TASK_15 -->

<!-- START_TASK_16 -->
### Task 16: Bootstrap runbook + incident-response skeleton + SIEM export runbook

**Verifies:** `rac-v1.AC10.5` (documentation/operational)

**Files:**
- Create: `/home/sysop/rac/docs/runbooks/bootstrap.md`
- Create: `/home/sysop/rac/docs/runbooks/incident-response.md`
- Create: `/home/sysop/rac/docs/runbooks/siem-export.md`

**Implementation:**

`bootstrap.md` is the Tier 1 manual runbook. Sections:

1. **Prerequisites** — cloud admin has Owner on target subscription; Entra Global Admin or Application Administrator for app registrations; DNS delegation authority for `${PARENT_DOMAIN}`.
2. **Subscription setup** — register resource providers: `Microsoft.App`, `Microsoft.ContainerRegistry`, `Microsoft.DBforPostgreSQL`, `Microsoft.KeyVault`, `Microsoft.Storage`, `Microsoft.OperationalInsights`, `Microsoft.Insights`, `Microsoft.Cdn`, `Microsoft.Network`, `Microsoft.Security`. Enable Microsoft Defender for Containers on the subscription.
3. **Entra app registrations** — create three apps: (a) Control Plane OIDC (delegated) for researcher sign-in, (b) Control Plane API (protected resource, exposes scope `api://rac-control-plane/submit`), (c) RAC Infra Deploy (for GHA OIDC). Record client IDs + tenant ID.
4. **Federated identity credential** — for the GHA Infra Deploy app, add federated credentials per environment: `repo:<org>/rac:environment:dev`, `:staging`, `:prod`. Grant Owner on each subscription.
5. **Bootstrap Key Vault** — create a pre-platform Key Vault (`kv-rac-bootstrap`) in a resource group the platform deploy does not manage. Populate: `pg-admin-password-<env>` (generated), `appgw-cert-<env>` (BYO PFX or managed cert). Grant the GHA deploy principal `Key Vault Secrets User` on this vault.
6. **DNS delegation** — once the platform deploy creates the child DNS zone, update the parent zone's NS records to point at the child zone's nameservers (output from the deploy).
7. **GH Environment setup** — create `dev`, `staging`, `prod` GitHub Environments. Set required reviewers on `staging`, `prod`. Set environment secrets and variables per the workflow YAML header.
8. **First deploy** — run `infra-deploy` workflow against `dev`. Run `what-if` afterwards; confirm zero drift (AC1.2).
9. **Post-deploy TLS cert wiring** — if using Azure Front Door managed cert for `*.${PARENT_DOMAIN}`, validate the domain via TXT record dance. If using Key Vault-imported cert for App Gateway, ensure App Gateway's managed identity has `Key Vault Certificates User` on the bootstrap vault.
10. **Defender for Containers** — enable Defender plan for Containers on the subscription (UI or `az security pricing create`). Verify ACR scanning is active after first image push in Phase 3.

`incident-response.md` is a skeleton — placeholders for: shim 5xx spike, Control Plane 5xx spike, Postgres connection failure, Key Vault access denied, pipeline workflow stuck, suspicious token activity. Each placeholder has a `## Triage`, `## Containment`, `## Recovery`, `## Post-mortem` sub-structure with TODOs noting Phase 6/7 will flesh out alert wiring and specific query links. Real runbook text expands with Phase 6/7 deliveries.

`siem-export.md` documents the Event Hub SIEM export surface (AC10.5). Sections:

1. **Overview** — the Event Hub namespace `evhns-rac-<env>` contains two hubs: `eh-rac-access-logs` (Shim access events) and `eh-rac-approval-events` (Control Plane approval FSM events). Log Analytics diagnostic settings forward the corresponding custom tables to these hubs automatically (no application code changes required to consume them).
2. **Consumer prerequisites** — the SIEM subscriber needs the `Listen` authorization rule connection string for the namespace, stored in the platform Key Vault as `eh-listener-connstring`. Obtaining it: `az keyvault secret show --vault-name kv-rac-<env> --name eh-listener-connstring --query value -o tsv`.
3. **Connecting a test consumer** — commands to validate the hub is reachable and events are flowing:
   ```bash
   # Install Azure Event Hubs CLI extension
   az extension add --name eventhubs
   # Peek events (requires 'Listen' authz rule)
   az eventhubs eventhub message receive \
     --namespace-name evhns-rac-<env> \
     --eventhub-name eh-rac-access-logs \
     --resource-group rg-rac-<env> \
     --count 5
   ```
4. **Event schema** — both hubs emit JSON Lines matching the `structlog` output schema (fields: `timestamp`, `correlation_id`, `event_type`, `app_slug`, `submission_id`, `actor_principal_id`). Schema subject to extension in Phase 6 (Shim) and Phase 5 (approval events) but always includes these core fields.
5. **Quota and retention** — Standard SKU, 1 throughput unit, 24-hour message retention by default. Operator may increase retention or throughput units via the Azure Portal; no Bicep changes required.
6. **Acceptance test for AC10.5** — after Phase 1 deploy, trigger any submission-related action in Phase 2 dev, wait for the Log Analytics custom table to receive an entry, then run the peek command above and confirm the event arrives in the hub. Record the event JSON in the Phase 1 acceptance report.

**Verification:**
```bash
test -f /home/sysop/rac/docs/runbooks/bootstrap.md
test -f /home/sysop/rac/docs/runbooks/incident-response.md
test -f /home/sysop/rac/docs/runbooks/siem-export.md
# Lint markdown (optional): npx --yes markdownlint-cli2 docs/runbooks/*.md
```

**Commit:** `docs: Tier 1 bootstrap + incident-response skeleton + SIEM export runbook (AC10.5)`
<!-- END_TASK_16 -->

<!-- END_SUBCOMPONENT_F -->

<!-- START_TASK_17 -->
### Task 17: End-to-end verification on dev (operational acceptance)

**Verifies:** `rac-v1.AC1.1`, `rac-v1.AC1.2`, `rac-v1.AC1.3`, `rac-v1.AC1.5`, `rac-v1.AC11.1` (Tier 2), `rac-v1.AC10.3`, `rac-v1.AC10.5`

**Files:** None (verification task — no file changes)

**Implementation:**

This task runs the deploy against a real dev Azure subscription via the GHA workflow and confirms all ACs. The executing engineer must coordinate with the Moffitt cloud admin (operator) to run the workflow; the runbook in Task 16 documents prerequisites.

Required checks:

1. **AC1.1** — Trigger `infra-deploy.yml` via `workflow_dispatch` with `environment=dev`. Confirm the `deploy-dev` job completes without manual intervention beyond the GH Environment approval. Record the deployment name and resource group name.

2. **AC1.2** — Re-run the `whatif-dev` job on the same HEAD. Output must show "no changes" (or Azure's equivalent "Resource group properties would be updated" only for metadata that legitimately drifts, e.g., `provisioningState` — this is a known noisy diff; if any resource creation/update/deletion appears, the idempotency property is broken and the module must be fixed).

3. **AC1.3** — Observe that the `deploy-staging` and `deploy-prod` jobs are blocked pending reviewer approval in GitHub Actions. Approval must be required from a reviewer other than the workflow trigger author.

4. **AC1.4** — Temporarily remove `parentDomain` from `dev.bicepparam` in a scratch branch and run `az deployment sub what-if` locally. Confirm Azure returns an error naming `parentDomain` as missing. Revert.

5. **AC1.5** — After successful deploy, run:
   ```bash
   az resource list \
     --resource-group rg-rac-dev \
     --query "[?tags.rac_env!='dev'].{name:name, type:type}" \
     -o table
   ```
   Output must be empty — every resource has `rac_env=dev`.

6. **AC11.1 (Tier 2)** — Same tag query extended:
   ```bash
   az resource list \
     --resource-group rg-rac-dev \
     --query "[?tags.rac_env==null || tags.rac_managed_by==null]" \
     -o table
   ```
   Output must be empty.

7. **AC10.3 — alert smoke test** — trigger the controlled fault injection documented in `docs/runbooks/incident-response.md`: post 100 requests returning 503 to the dev shim endpoint, wait 5 minutes, confirm the action group's email or webhook was activated (visible in Azure portal: Monitor → Alerts → Alert history, or in action group's test notification history). If ACA apps are not yet deployed (Phase 2/6 not run), document that the Shim/Control Plane metric alerts are deferred; only Postgres and Key Vault alerts are verifiable at this stage.

8. **AC10.5 — SIEM Event Hub smoke test** — after Phase 2 is deployed to dev, trigger one submission event, wait for Log Analytics ingestion (up to 5 min), then run:
   ```bash
   az eventhubs eventhub message receive \
     --namespace-name evhns-rac-dev \
     --eventhub-name eh-rac-access-logs \
     --resource-group rg-rac-dev \
     --count 5
   ```
   Confirm at least one event arrives. Record the event JSON in the acceptance report. If Phase 2 is not yet deployed, document this check as deferred to Phase 2 operational verification.

Write findings to `${SCRATCHPAD_DIR}/phase1-acceptance-report.md` (where `SCRATCHPAD_DIR=/tmp/plan-2026-04-23-rac-v1-2a182fda`), committing only a summary line to the phase log. Full report stays in scratchpad per SCRATCHPAD_DIR convention.

**Verification (above commands are the verification).**

**Commit:** None (no file changes). If the what-if drift investigation requires a module fix, fixes are committed per-module.
<!-- END_TASK_17 -->

---

## Phase 1 Done Checklist

- [ ] `az bicep build` succeeds on `infra/main.bicep` and every module (verified in Tasks 3-13)
- [ ] `az deployment sub what-if` against a clean dev subscription produces a valid plan (AC1.1 precondition)
- [ ] `az deployment sub create` in GHA against dev succeeds (AC1.1)
- [ ] `az deployment sub what-if` after successful deploy shows zero changes (AC1.2)
- [ ] `staging` and `prod` jobs in GHA require reviewer approval (AC1.3)
- [ ] Missing-parameter deploys surface the parameter name (AC1.4)
- [ ] All Tier 2 resources carry `rac_env` tag at creation (AC1.5, AC11.1 partial)
- [ ] Both `rg-rac-<env>` and `rg-rac-tier3-<env>` resource groups exist with correct tags (Task 13)
- [ ] Managed identity module compiled + outputs wired in `main.bicep` (Task 12B)
- [ ] 5 metric alerts + action group deployed; fault-injection smoke test passes (AC10.3, Task 12C/17)
- [ ] Event Hub namespace + 2 hubs + diagnostic settings deployed; test consumer receives events (AC10.5, Task 12D/17)
- [ ] Bootstrap + incident-response + siem-export runbooks exist (Task 16)
- [ ] Phase 1 acceptance report saved to `${SCRATCHPAD_DIR}/phase1-acceptance-report.md` (Task 17)

**Phase 1 complete when all checkboxes are satisfied on a real dev subscription.**
