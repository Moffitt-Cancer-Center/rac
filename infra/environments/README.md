# RAC Infrastructure Parameter Files

This directory contains per-environment parameter files (`.bicepparam`) for the RAC platform Bicep composition. Each file defines values specific to a deployment environment (dev, staging, prod).

## File Structure

- `dev.bicepparam` — Development environment parameters
- `staging.bicepparam` — Staging environment parameters  
- `prod.bicepparam` — Production environment parameters

## Placeholder Values

Each parameter file contains several placeholders that must be replaced before deployment:

### Azure Subscription & Tenant
- `idpTenantId` — Your Entra tenant ID (e.g., `12345678-1234-1234-1234-123456789012`)
- `location` — Azure region code (e.g., `eastus`, `westus2`)

### Globally Unique Resource Names

Azure requires certain resource names to be globally unique across all Azure tenants. Replace these with values that uniquely identify your deployment:

- `acrName` — Azure Container Registry name (3-50 alphanumeric, no hyphens)
- `storageAccountName` — Storage account name (3-24 lowercase alphanumeric, no hyphens)
- `pgServerName` — Postgres Flexible Server name

Example naming scheme: `rac<env><suffix>` (e.g., `racdevst001`, `racstaging001`).

### DNS Configuration
- `parentDomain` — The parent DNS domain for your deployment (e.g., `rac-dev.example.org`, `rac.example.org`)
  - The platform creates a public DNS zone at this name
  - Parent-zone delegation (NS records) is configured manually during Tier 1 bootstrap
  - See `docs/runbooks/bootstrap.md` for DNS delegation steps

### Secret References via Key Vault

The parameter files reference secrets stored in a separate **bootstrap Key Vault** (not the platform Key Vault created by this deployment). This pattern keeps credentials out of source control.

Two secrets are referenced using the `getSecret()` function:

#### `pgAdminPassword`
- **Secret name:** `pg-admin-password-<env>` (e.g., `pg-admin-password-dev`)
- **Storage location:** Bootstrap Key Vault (pre-created, operator-managed)
- **Content:** A strong random password for the Postgres administrator login
- **Security:** Only the deploying principal (GHA service principal) needs `Key Vault Secrets User` access on the bootstrap vault

#### `appGwTlsCertKvSecretId`
- **Secret name:** `appgw-cert-<env>` (e.g., `appgw-cert-dev`)
- **Storage location:** Bootstrap Key Vault
- **Content:** A PFX certificate in Base64 encoding for App Gateway TLS
- **Format:** Full versioned secret URI: `https://<kv-name>.vault.azure.net/secrets/<secret-name>/<version>`
- **Options:**
  - Manually imported PFX certificate (BYO)
  - Azure-managed TLS certificate via Front Door (operator configures)

### How `getSecret()` Works

`getSecret()` is a Bicep function available only in parameter files (`.bicepparam`). It:

1. Fetches the secret value from Key Vault at deployment time
2. Returns the plain-text value to Bicep (the secret material is not visible in source)
3. Requires the deploying principal to have `Key Vault Secrets User` role on the bootstrap vault

**Example usage:**
```bicep
param pgAdminPassword = getSecret(
  '<subscription-id>',
  '<bootstrap-rg-name>',
  '<bootstrap-kv-name>',
  'pg-admin-password-dev'
)
```

### Environment-Specific Differences

#### Dev (`dev.bicepparam`)
- **Network:** Single VNet with minimal redundancy
- **Postgres:** Burstable tier (Standard_B2s), no HA, 32 GB storage, 7-day backups
- **ACA:** No zone redundancy, Consumption profile only
- **Key Vault:** Purge protection disabled, 7-day soft-delete retention (allows cleanup)
- **Storage:** LRS (local redundancy, cost-effective)

#### Staging (`staging.bicepparam`)
- **Network:** Same as prod (multi-AZ ready)
- **Postgres:** General Purpose tier (Standard_D2s_v3), same-zone HA, 64 GB storage, 14-day backups
- **ACA:** Zone redundancy enabled
- **Key Vault:** Defaults (purge protection true, 90-day retention)

#### Prod (`prod.bicepparam`)
- **Network:** Multi-AZ, fully redundant
- **Postgres:** General Purpose tier (Standard_D4s_v3), zone-redundant HA, 128 GB storage, 35-day backups
- **ACA:** Zone redundancy, D4 dedicated profile
- **Key Vault:** Full compliance posture (purge protection true, 90-day retention)
- **Storage:** GRS (geo-redundant)

## Deployment

### Via GitHub Actions Workflow
```bash
# Automatic: workflow triggers on push to infra/**
# Manual: workflow_dispatch from GitHub UI selects environment (dev/staging/prod)
```

### Local/Manual Deployment
```bash
# Validate parameter file
az bicep build-params --file environments/dev.bicepparam

# Preview changes
az deployment sub what-if \
  --location <region> \
  --template-file main.bicep \
  --parameters environments/dev.bicepparam

# Deploy
az deployment sub create \
  --name rac-dev-<timestamp> \
  --location <region> \
  --template-file main.bicep \
  --parameters environments/dev.bicepparam
```

## Key Vault Integration

The platform creates its own Key Vault (`kv-rac-<env>`) for runtime secrets (database credentials, certificate keys, etc.). This is separate from the bootstrap Key Vault referenced in parameter files.

### Runtime Secrets (Platform Key Vault)
- Certificate private keys
- Database credentials (wired to Postgres)
- SIEM Event Hub connection string (output to `eh-listener-connstring`)

### Bootstrap Secrets (Bootstrap Key Vault)
- Postgres admin password
- App Gateway TLS certificate PFX

## Two-Pass Deployment Pattern

On the first Phase 1 deploy, the managed identities are created but the Control Plane ACA app doesn't exist yet. The `controlPlaneIdentityPrincipalId` parameter is left empty, skipping the DNS Zone role assignment.

After Phase 2 deploys the Control Plane app:

1. Capture the Control Plane managed identity principal ID from Phase 1 main.bicep output: `controlPlaneMiPrincipalId`
2. Update the bicepparam file: `param controlPlaneIdentityPrincipalId = '<principal-id>'`
3. Re-run the deploy with the updated parameter

This is documented in `docs/runbooks/bootstrap.md`.

## Troubleshooting

### Missing Placeholder Error
If you see `Error: Missing required parameter 'parentDomain'`:
- The bicepparam file is incomplete
- Check that all placeholders have been replaced with real values
- Run `az bicep build-params --file environments/<env>.bicepparam` to validate syntax

### Key Vault Access Error
If `getSecret()` fails during deployment:
- The deploying principal (GHA service principal or user account) doesn't have `Key Vault Secrets User` on the bootstrap vault
- Grant the role via: `az role assignment create --role 'Key Vault Secrets User' --assignee <principal-id> --scope <kv-id>`

### Globally Unique Name Conflict
If ACR or Storage Account creation fails with name already taken:
- Choose a different `acrName` or `storageAccountName` (globally unique within Azure)
- Common pattern: add a random suffix (e.g., `racdevst001`) or timestamp

## References

- Architecture: `docs/design-plans/2026-04-23-rac-v1.md`
- Bootstrap runbook: `docs/runbooks/bootstrap.md`
- Bicep parameter file spec: https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/parameter-files
