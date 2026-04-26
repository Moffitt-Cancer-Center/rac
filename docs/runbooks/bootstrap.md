# RAC Bootstrap Runbook (Tier 1)

This runbook documents the manual setup required to deploy RAC for the first time. It assumes the executing operator has Owner access to target Azure subscriptions and appropriate Entra permissions.

## Prerequisites

- **Azure:** Owner role on target subscription(s): dev, staging, prod
- **Entra:** Global Administrator or Application Administrator role for app registrations
- **DNS:** Authority to add NS records at your parent domain (e.g., delegation from `example.org` to `rac.example.org`)
- **Tools:** `az` CLI (≥ 2.86), `git`, text editor
- **GitHub:** Admin access to the RAC repo to configure Environments and secrets

## 1. Subscription Setup

Register required resource providers and enable security features on each target subscription.

```bash
az account set --subscription <SUBSCRIPTION_ID>

# Register providers
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.Insights
az provider register --namespace Microsoft.Cdn
az provider register --namespace Microsoft.Network
az provider register --namespace Microsoft.Security

# Wait for providers to finish registering (typically 1-2 minutes)
az provider list --query "[?registrationState == 'NotRegistered']" -o table

# Enable Microsoft Defender for Containers (required for ACR scanning)
az security pricing create --resource-type 'Containers' --pricing-tier Standard
```

## 2. Entra App Registrations

Create three app registrations in your Entra directory. Record the client IDs and tenant ID for later.

### App 1: Control Plane OIDC (delegated — researcher sign-in)

```bash
az ad app create \
  --display-name 'RAC Control Plane (OIDC)' \
  --sign-in-audience AzureADMyOrg \
  --public-client-redirect-uris 'http://localhost:3000/callback'

# Record the client ID (appId) from the output
# Note: This app will be used for delegated auth (researcher login). 
# Configure Web platform redirect URIs in the Portal to match your Control Plane frontend URLs.
```

### App 2: Control Plane API (protected resource)

```bash
az ad app create \
  --display-name 'RAC Control Plane (API)' \
  --sign-in-audience AzureADMyOrg

# Record the client ID
# In the Portal, add an API Scope: 'api://rac-control-plane/submit'
# Create API Permissions: delegated 'submit' scope
```

### App 3: RAC Infra Deploy (service principal for GHA)

```bash
az ad app create \
  --display-name 'RAC Infra Deploy' \
  --sign-in-audience AzureADMultipleOrgs  # Service principal, not delegated

# Record the client ID
# Create a service principal:
az ad sp create --id <APP_CLIENT_ID>

# Grant Owner on each subscription:
for SUB in dev staging prod; do
  SUB_ID=$(az account show --subscription $SUB --query id -o tsv)
  az role assignment create \
    --role Owner \
    --assignee <SERVICE_PRINCIPAL_OBJECT_ID> \
    --scope /subscriptions/$SUB_ID
done
```

## 3. Federated Identity Credentials for GHA

For the RAC Infra Deploy service principal, add federated credentials so GHA can authenticate without storing secrets.

```bash
az ad app federated-credential create \
  --id <APP_CLIENT_ID> \
  --parameters '{
    "name": "rac-env-dev",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<ORG>/rac:environment:dev",
    "audiences": ["api://AzureADTokenExchange"]
  }'

az ad app federated-credential create \
  --id <APP_CLIENT_ID> \
  --parameters '{
    "name": "rac-env-staging",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<ORG>/rac:environment:staging",
    "audiences": ["api://AzureADTokenExchange"]
  }'

az ad app federated-credential create \
  --id <APP_CLIENT_ID> \
  --parameters '{
    "name": "rac-env-prod",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<ORG>/rac:environment:prod",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

## 4. Bootstrap Key Vault

Create a pre-platform Key Vault to hold secrets that the infrastructure deploy needs. This vault is separate from the platform Key Vault that Bicep creates. The deploying principal must have access.

```bash
# Create a resource group for bootstrap infrastructure
az group create --name rg-rac-bootstrap --location eastus

# Create the bootstrap Key Vault
az keyvault create \
  --resource-group rg-rac-bootstrap \
  --name kv-rac-bootstrap-001 \
  --location eastus \
  --enable-rbac-authorization

# Generate a strong password for Postgres admin
PG_ADMIN_PASSWORD=$(openssl rand -base64 32)

# Store it in the bootstrap vault (dev environment)
az keyvault secret set \
  --vault-name kv-rac-bootstrap-001 \
  --name pg-admin-password-dev \
  --value "$PG_ADMIN_PASSWORD"

# Do the same for staging and prod
az keyvault secret set \
  --vault-name kv-rac-bootstrap-001 \
  --name pg-admin-password-staging \
  --value "$(openssl rand -base64 32)"

az keyvault secret set \
  --vault-name kv-rac-bootstrap-001 \
  --name pg-admin-password-prod \
  --value "$(openssl rand -base64 32)"

# For App Gateway TLS certificates:
# If using self-signed certs for testing:
for ENV in dev staging prod; do
  openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
    -subj "/CN=*.rac-${ENV}.example.org"
  # Convert to PFX
  openssl pkcs12 -export -in cert.pem -inkey key.pem -out cert.pfx \
    -passout pass:changeme
  # Import to Key Vault
  az keyvault certificate import \
    --vault-name kv-rac-bootstrap-001 \
    --name appgw-cert-${ENV} \
    --file cert.pfx \
    --password changeme
done

# Grant GHA deploy principal access to this vault
az role assignment create \
  --role 'Key Vault Secrets User' \
  --assignee <SERVICE_PRINCIPAL_OBJECT_ID> \
  --scope /subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-rac-bootstrap/providers/Microsoft.KeyVault/vaults/kv-rac-bootstrap-001
```

## 5. DNS Delegation

After running the first infrastructure deploy, it creates a child DNS zone. Delegate that zone from your parent domain.

```bash
# After infra-deploy completes, the outputs include dnsZoneNameServers
# Query the created zone:
az network dns zone show \
  --name rac.example.org \
  --resource-group rg-rac-dev \
  --query nameServers -o tsv

# Example output:
# ns1-01.azure-dns.com.
# ns2-01.azure-dns.net.
# ns3-01.azure-dns.org.
# ns4-01.azure-dns.info.

# In your parent domain registrar (or parent Azure DNS zone), create NS records:
# rac.example.org. NS ns1-01.azure-dns.com.
#                  NS ns2-01.azure-dns.net.
#                  NS ns3-01.azure-dns.org.
#                  NS ns4-01.azure-dns.info.

# Verify delegation:
nslookup -type=NS rac.example.org
```

## 6. GitHub Environments, Secrets, and Deploy Credentials

### GitHub Secrets Setup

In the GitHub repo Settings → Secrets and variables → Actions, create the following secrets (repository-level, accessible to all workflows):

- `RAC_PG_ADMIN_PASSWORD`: The Postgres admin password from bootstrap vault (see step 4)
- `RAC_APPGW_TLS_CERT_KV_SECRET_ID`: The full versioned Key Vault secret URI for the App Gateway TLS certificate

Example:
```
RAC_PG_ADMIN_PASSWORD = "abc123...xyz" (from kv-rac-bootstrap-001 pg-admin-password-dev)
RAC_APPGW_TLS_CERT_KV_SECRET_ID = "https://kv-rac-bootstrap-001.vault.azure.net/secrets/appgw-cert-dev/abc123..."
```

The infra-deploy workflow will inject these as environment variables during deployment, which are read by the bicepparam files via `readEnvironmentVariable()`.

### GitHub Environments

In the GitHub repo Settings → Environments, create three environments and configure secrets/variables.

### Environment: dev

- **Deployment branches:** None (allow all)
- **Required reviewers:** None (auto-deploy)
- **Secrets:**
  - `AZURE_CLIENT_ID`: RAC Infra Deploy app client ID
  - `AZURE_TENANT_ID`: Entra tenant ID
  - `AZURE_SUBSCRIPTION_ID_DEV`: Dev subscription ID
- **Variables:**
  - `AZURE_LOCATION`: eastus (or your region)

### Environment: staging

- **Deployment branches:** main only
- **Required reviewers:** 1-2 team members with deploy authority
- **Secrets:**
  - `AZURE_CLIENT_ID`: RAC Infra Deploy app client ID
  - `AZURE_TENANT_ID`: Entra tenant ID
  - `AZURE_SUBSCRIPTION_ID_STAGING`: Staging subscription ID
- **Variables:**
  - `AZURE_LOCATION`: eastus

### Environment: prod

- **Deployment branches:** main only
- **Required reviewers:** 2+ senior team members
- **Secrets:**
  - `AZURE_CLIENT_ID`: RAC Infra Deploy app client ID
  - `AZURE_TENANT_ID`: Entra tenant ID
  - `AZURE_SUBSCRIPTION_ID_PROD`: Prod subscription ID
- **Variables:**
  - `AZURE_LOCATION`: eastus
- **Secrets:**
  - `RAC_PG_ADMIN_PASSWORD`: (same as repository secret)
  - `RAC_APPGW_TLS_CERT_KV_SECRET_ID`: (same as repository secret)

(These secrets override the repository-level ones for prod deployment if tighter control is needed.)

## 7. First Deploy

Push a commit that touches `infra/**` to main. The `infra-deploy` workflow will trigger.

```bash
# Ensure you're on main and have committed all changes
git push origin main

# Monitor the workflow in GitHub Actions
# The whatif-dev job runs first (sanity check)
# Then deploy-dev runs
# staging and prod will block pending manual approval
```

If the deploy fails with a missing parameter error, read the error message carefully. The parameter name is clearly stated. Update the relevant `.bicepparam` file and retry.

## 8. Post-Deploy Steps

### Verify Infrastructure

```bash
# List deployed resources
az resource list \
  --resource-group rg-rac-dev \
  --query "[].{name:name, type:type}" \
  -o table

# Confirm Tier 3 resource group exists
az group show --name rg-rac-tier3-dev

# Capture Key Vault URI for later use
KV_URI=$(az deployment sub show \
  --name rac-dev-<RUN_ID> \
  --query properties.outputs.keyVaultUri.value -o tsv)
echo "Key Vault: $KV_URI"
```

### Postgres UUID extension

Migration 0001 creates the `uuid-ossp` extension and defines a `uuidv7()` SQL wrapper that delegates to `uuid_generate_v4()`. We use `uuid-ossp` (universally available) rather than `pg_uuidv7` because the latter is **not** on the Azure PG flexible-server `azure.extensions` allowlist for several regions (eastus2 confirmed 2026-04-25). No manual extension allowlisting is required for the bootstrap path. If a future deployment is in a region where `pg_uuidv7` is allowlisted and you want the time-ordering back, change the body of `uuidv7()` in a forward migration — column DDL is unchanged.

### Front Door Custom Domain Validation

Front Door custom domains require DNS TXT record validation. This is performed by Azure automatically.

1. After infra deploy completes, the Front Door custom domain is in `ValidationTokenNotFound` state.
2. Query the validation token:

```bash
az afd custom-domain show \
  --profile-name afd-rac-dev \
  --custom-domain-name rac-dev-wildcard \
  --resource-group rg-rac-dev \
  --query "validationProperties.validationToken" -o tsv
```

3. Add a TXT record to your DNS zone (in Azure DNS or at your registrar):

```
_dnsauth.rac-dev.example.org  TXT  <validation-token-from-step-2>
```

4. Wait for validation (typically 5-15 minutes). Re-query the custom domain state:

```bash
az afd custom-domain show \
  --profile-name afd-rac-dev \
  --custom-domain-name rac-dev-wildcard \
  --resource-group rg-rac-dev \
  --query "domainValidationState"
```

State should change from `ValidationTokenNotFound` to `Approved`. Once approved, HTTPS traffic to `*.rac-dev.example.org` is routed through Front Door to App Gateway.

### TLS Certificate Setup

If using App Gateway with a Key Vault-referenced certificate:

1. The certificate is already stored in the bootstrap Key Vault.
2. Grant App Gateway's managed identity `Key Vault Certificates User` on the bootstrap vault (note: the role assignment is created in-repo by the Bicep infrastructure):

```bash
# This is handled automatically by the infrastructure code (role-assignments.bicep)
# No manual action required for the GHA deploy principal.
# The appgw-rac-dev managed identity already has certificates access.
```

### Defender for Containers Verification

```bash
# Confirm Defender is enabled
az security pricing list --query "[?name == 'Containers']" -o table

# After the first container image is pushed to ACR (Phase 3), 
# Defender will scan it. Monitor in the Azure Portal:
# Container Registry → Security → Vulnerabilities
```

## 9. Phase 5 Bridge Re-deploy

After Phase 5 provisioning completes (see `docs/implementation-plans/2026-04-23-rac-v1/phase_05.md`), the Control Plane managed identity's principal ID will be available as a deploy output. Re-run the infra-deploy workflow to enable the DNS Zone Contributor role assignment on the child DNS zone.

Capture the Control Plane MI principal ID from the Phase 1 deployment outputs:

```bash
az deployment sub show \
  --name rac-dev-<RUN_ID> \
  --query "properties.outputs.controlPlaneMiPrincipalId.value" -o tsv
```

Then re-run the infra-deploy workflow with the `controlPlaneIdentityPrincipalId` parameter supplied (this parameter is currently empty and the DNS role assignment is skipped):

```bash
# Via the CLI (or trigger via GitHub Actions workflow_dispatch)
az deployment sub create \
  --location $AZURE_LOCATION \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters controlPlaneIdentityPrincipalId=<output-from-phase5> \
  pgAdminPassword=$RAC_PG_ADMIN_PASSWORD \
  appGwTlsCertKvSecretId=$RAC_APPGW_TLS_CERT_KV_SECRET_ID
```

This second deploy is idempotent: all other resources remain unchanged; only the conditional DNS Zone Contributor role assignment is created.

## 9b. Phase 2 Control Plane Deployment

After Phase 2 ships, the deploy sequence is:

1. **Build + push the control-plane image to ACR.** The platform ACR has
   `publicNetworkAccess: 'Disabled'`, so a `docker push` from a workstation
   fails until the registry is reachable. The fastest workaround for a
   first-deploy from a personal laptop is to temporarily enable public
   network access for the push window only:

   ```bash
   ACR=$(az acr list --resource-group rg-rac-dev --query "[0].name" -o tsv)
   az acr update --name "$ACR" --public-network-enabled true
   az acr login --name "$ACR"
   docker push "$ACR.azurecr.io/rac-control-plane:dev-001"
   az acr update --name "$ACR" --public-network-enabled false
   ```

   Long-term answer: build in CI inside the VNet (self-hosted runner) or
   use ACR Tasks with private endpoint already wired. The KV has the same
   constraint for operator-driven `az keyvault secret set` calls — open it
   briefly, seed the secret, close it.

2. **Seed control-plane secrets in the platform KV** (not the bootstrap
   KV). Today the control-plane container reads `rac-pg-admin-password`
   directly because the deployed image authenticates as `rac_admin` for
   smoke-test purposes. Copy it from the bootstrap KV:

   ```bash
   PLATFORM_KV=$(az keyvault list --resource-group rg-rac-dev --query "[0].name" -o tsv)
   PG_ADMIN=$(az keyvault secret show --vault-name kv-rac-bootstrap-001 \
     --name pg-admin-password-dev --query value -o tsv)
   az keyvault secret set --vault-name "$PLATFORM_KV" \
     --name rac-pg-admin-password --value "$PG_ADMIN"
   ```

   When the follow-up that switches the control plane to `rac_app` lands,
   replace this with a dedicated `rac-app-db-password` secret backed by the
   actual `rac_app` LOGIN role.

3. **Run alembic migrations.** Migrations are baked into the control-plane
   image. After the ACA app is deployed and healthy, exec into it:

   ```bash
   az containerapp exec --name ca-rac-cp-dev --resource-group rg-rac-dev \
     --command "alembic upgrade head"
   ```

   This applies all 12 migrations including 0009 which creates `rac_app`
   (NOLOGIN placeholder) and `rac_shim` (LOGIN, no password set yet).
   Verify `/health` returns `{"status":"healthy",...}` afterwards.

## 10. Phase 6 Shim Deployment

After Phase 6 ships (migration 0009 applied, shim image pushed to ACR), complete
the following steps to activate the Token-Check Shim.

### 10a. Set the rac_shim role password

Migration 0009 creates the `rac_shim` Postgres role with a placeholder password.
The placeholder is intentionally invalid — the shim will refuse to connect until
you set a real password.

Connect to the Postgres server as the admin role and run:

```sql
ALTER ROLE rac_shim WITH PASSWORD '<strong-random-secret>';
```

Generate a strong random secret (at least 32 bytes of entropy):

```bash
openssl rand -base64 32
```

### 10b. Store the DSN in Key Vault

Build the full DSN that the shim will use and store it in the platform Key Vault:

```bash
# Retrieve the Postgres FQDN from Azure
PG_HOST=$(az postgres flexible-server show \
  --resource-group rg-rac-dev \
  --name <pg-server-name> \
  --query "fullyQualifiedDomainName" -o tsv)

SHIM_DSN="postgresql+asyncpg://rac_shim:<password>@${PG_HOST}:5432/<db-name>?ssl=require"

# Store in the platform Key Vault under the name shim-database-dsn
KV_NAME=$(az keyvault list \
  --resource-group rg-rac-dev \
  --query "[0].name" -o tsv)

az keyvault secret set \
  --vault-name "$KV_NAME" \
  --name shim-database-dsn \
  --value "$SHIM_DSN"
```

Also store the cookie HMAC secret (used to sign the `rac_session` cookie):

```bash
HMAC_SECRET=$(openssl rand -base64 32)

az keyvault secret set \
  --vault-name "$KV_NAME" \
  --name shim-cookie-hmac \
  --value "$HMAC_SECRET"
```

### 10c. Re-deploy with shimImageName

After the shim image has been built and pushed to ACR (Phase 6 GHA pipeline),
re-run the infra-deploy workflow with `shimImageName` set:

```bash
SHIM_IMAGE="<acr-login-server>/rac-shim:v1.0"

az deployment sub create \
  --location $AZURE_LOCATION \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam \
  --parameters shimImageName="$SHIM_IMAGE" \
               shimIssuer="https://login.microsoftonline.com/<TENANT_ID>/v2.0" \
               shimCookieDomain=".<parent-domain>" \
               shimInstitutionName="Moffitt Cancer Center" \
  pgAdminPassword=$RAC_PG_ADMIN_PASSWORD \
  appGwTlsCertKvSecretId=$RAC_APPGW_TLS_CERT_KV_SECRET_ID
```

This deploy:
1. Creates the `rac-shim-dev` Container App with `min-replicas=1`.
2. Updates the App Gateway backend pool to target the shim's internal FQDN.
3. Wires Key Vault secret references for `shim-database-dsn` and `shim-cookie-hmac`.

### 10d. Key Vault access note

The shim's managed identity (`id-rac-shim-<env>`) needs `Key Vault Secrets User`
on the platform Key Vault to resolve the `shim-database-dsn` and `shim-cookie-hmac`
secrets at startup.  This role assignment is already created by
`infra/modules/role-assignments.bicep` (Phase 1) — no manual action is required.

## 11. Idempotency Check

Run `infra-deploy` again (or trigger manually) against the same subscription.

```bash
# Trigger via workflow_dispatch with environment=dev
# Or push another commit to infra/
```

The `whatif-dev` job should report zero changes. If resources appear to be recreated or modified unexpectedly, this indicates a drift or non-idempotent module. Stop and investigate.

## Troubleshooting

**"Missing parameter 'parentDomain'"**
- Update `infra/environments/dev.bicepparam` with your parent domain (e.g., `rac.example.org`).
- Ensure it matches the DNS zone you control.

**"The image reference 'appgwtlscertkvsecretid' is invalid"**
- App Gateway secret reference format: `https://<kv-name>.vault.azure.net/secrets/<secret-name>/<version>`
- Verify the secret exists in bootstrap Key Vault and the URL is correct.

**"Federated credential not found"**
- Ensure federated credentials are created under the correct app registration.
- Run `az ad app federated-credential list --id <APP_ID>` to verify.

**"Cannot delete resource group rg-rac-dev: locks exist"**
- Resource locks may be in place (e.g., Key Vault purge protection).
- Phase 1 sets purge protection to true for prod only; dev allows purge.
- To manually clean up dev for testing, update `kvEnablePurgeProtection=false` in `dev.bicepparam`, redeploy, then delete.

## Common deploy gotchas

These are bugs that surfaced during the first end-to-end deploy and will surface again on any deploy in a fresh subscription/region. Run `scripts/infra-validate.sh dev` before pushing — it catches most of them at compile/validate time without burning a 25-min teardown cycle.

**Region offer restrictions on personal/trial subscriptions.**
- `Microsoft.DBforPostgreSQL` is offer-restricted in `eastus` for personal/trial subs. Use `eastus2` or request a quota increase. Probe via `az postgres flexible-server list-skus --location <region>` (returns empty if restricted).
- `pg_uuidv7` is **not** in the `azure.extensions` allowlist for some regions/PG versions (notably `eastus2`). Migration 0001 already uses `uuid-ossp` with a `uuidv7()` SQL wrapper that emits v4 UUIDs, so no override is needed. If you ever change the migration back to require `pg_uuidv7`, check the allowlist first via `az postgres flexible-server parameter show --resource-group <rg> --server-name <pg> --name azure.extensions --query "allowedValues"`.

**Soft-delete name reservations.**
- Key Vault: 7–90 day reservation on names after deletion. Identical re-deploy in the same subscription/RG hits the same `uniqueString` hash and collides. `scripts/teardown.sh` purges KVs matching the env tag, but only for KVs that were created with that tag. Manually purge via `az keyvault purge --name <kv> --location <region>` if needed.
- Storage Accounts: 30-day reservation; **no purge command exists**. Either wait or change the `racEnv`/`uniqueString` seed.
- Postgres flexible servers: server names are global; the global namespace is densely populated. The `uniqueString`-based defaults in `main.bicep` produce stable names that collide on quick re-deploys after teardown. Wait or override the name.

**Naming rule divergences across resource types.**
- **Front Door WAF policies**: alphanumeric **only** (no hyphens). Use `wafrac${env}`, not `waf-rac-${env}`.
- **App Gateway WAF policies**: hyphens **allowed**. Different resource type, different rules.
- **Storage account names**: lowercase alphanumeric, **24 char max**. With `racEnv='staging'` and a 13-char hash, the default overflows — `main.bicep` uses `substring(uniqueString(...), 0, 10)` to fit.

**Bicep gotchas that don't surface until ARM apply.**
- `'''multi-line'''` strings do **not** support `${...}` interpolation — the literal text is preserved. Use a single-quoted string with `\n` line breaks for any KQL/JSON that needs param values substituted.
- BCP318 warnings on conditional-module access (`module.outputs.X` where `module` is `if`-gated) **are real bugs** — use the safe-dereference operator `module.?outputs.X ?? defaultValue`. `scripts/infra-validate.sh` treats BCP warnings as errors.
- `enablePurgeProtection: false` on `Microsoft.KeyVault/vaults` is **rejected** by Azure — the property accepts only `true` or omission. Use a ternary that emits `null` (which Bicep omits): `enablePurgeProtection: enablePurgeProtection ? true : null`.
- App Gateway HTTPS listener: set **either** `hostName` **or** `hostNames`, not both.
- Front Door WAF policy `sku.name` must match the targeting Front Door profile's tier (`Premium_AzureFrontDoor` for Premium profiles).
- Front Door `Microsoft_DefaultRuleSet` 1.x supports a ruleset-wide `ruleSetAction`; 2.x requires per-rule actions. Easy to mix up.
- `private endpoints + privateDnsZoneGroup` referencing a same-template `privateDnsZone`: declare the zone first, and add explicit `dependsOn: [privateDnsZone]` on the group. Otherwise ARM batches them in parallel and the group's `privateDnsZoneId` resolves to empty.
- Storage account child resources (`blobServices`, `containers`, `managementPolicy`, private endpoint) race the parent under network-restricted ACLs; serialize via explicit `dependsOn` chains.

**Cross-RG role assignments must live in their own module.**
- The App Gateway MI needs Secrets/Certificates User on the **bootstrap KV** (different RG from platform). Wire this via a module scoped to `resourceGroup('rg-rac-bootstrap')`. See `infra/modules/bootstrap-kv-rbac.bicep`.

**Private endpoints make first push/seed painful.**
- Both ACR and the platform KV ship with `publicNetworkAccess: 'Disabled'`. A `docker push` from a workstation, or `az keyvault secret set` for operator-managed secrets, fails until you punch a temporary hole. The intended pattern is `az acr update --public-network-enabled true`, push/seed, `az acr update --public-network-enabled false`. Same for KV via `az keyvault update --public-network-access Enabled`. Long-term: move pushes into a VNet-resident CI runner.

**Platform-MI role grants people forget.**
- ACA secret-resolution failures (control plane / shim never starting) are almost always a missing **Key Vault Secrets User** grant on the platform KV for the app's user-assigned MI. `role-assignments.bicep` grants Crypto User and Secrets User; if you bypass that module on a custom deploy you'll see opaque "secret not found" startup errors.
- `rac-control-plane:<tag>` and `rac-shim:<tag>` images only pull if the matching MIs hold **AcrPull** on the registry. `acr.bicep` issues both grants when `controlPlaneMiPrincipalId` and `shimMiPrincipalId` are non-empty.

**Telemetry alert KQL fails on a fresh Log Analytics workspace.**
- `alerts.bicep` references `ContainerAppConsoleLogs_CL.StatusCode_d` and the custom `RAC_PipelineLog_CL` table. Neither exists until traffic has flowed. The module gates these alerts on `deployTelemetryAlerts` (default `false`); flip to `true` on a follow-up deploy after logs accumulate, otherwise the deploy fails ARM validation with `InvalidQuery`.

**GitHub Actions OIDC + long Azure deploys.**
- GitHub's OIDC assertion is valid for **5 minutes**. Sync `az deployment sub create` against a deploy that takes >50 min will fail with `AADSTS700024` when az CLI tries to refresh. The fix is `--no-wait` + a polling loop (each poll completes inside the AAD token's 1-hour lifetime) — already wired into `.github/workflows/infra-deploy.yml`.

**First ACA environment in a fresh subscription takes 30–60 minutes** (Container Apps provisions an internal AKS fleet under the hood). Looks like a hang in the Portal. Subsequent deploys are fast. Plan accordingly on first stand-up.

## Next Steps

1. Confirm all Tier 2 infrastructure is operational (Task 17 acceptance checks).
2. Proceed to Phase 2: Control Plane skeleton (auth, submission schema, CRUD operations).
3. After Phase 5 completes, re-run `infra-deploy` with `controlPlaneIdentityPrincipalId` to wire up DNS Zone Contributor role (see section 9: Phase 5 Bridge Re-deploy).
