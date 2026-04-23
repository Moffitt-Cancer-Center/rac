# Cost Control Runbook: Dev-Cycle Spin-Up and Tear-Down

This runbook is for RAC engineers who need to deploy a dev environment, run acceptance tests, and tear it down without accumulating unnecessary Azure charges.

## Why This Exists

RAC's Tier 2 baseline includes several always-on premium SKUs that incur charges every hour the environment runs, regardless of traffic:

- **Front Door Premium** — global CDN and DDoS protection
- **App Gateway v2 WAF_v2** — ingress controller with web application firewall
- **Azure Container Registry Premium** — scanning, private linking, geo-replication
- **Postgres Flexible Server** — managed database (even idle instances cost money)
- **Log Analytics workspace** — pay-per-GB ingested
- **Event Hub Standard** — event streaming infrastructure
- **ACA Managed Environment** — consumption-based, but base infra costs apply

A dev environment running idle (no traffic) still incurs these charges. This runbook ensures the environment is only up when needed.

## Approximate Hourly and Monthly Costs for a Dev Environment

**Disclaimer:** These costs are approximate and derived from public Azure pricing as of 2026-04-23. Verify all figures with the [Azure Pricing Calculator](https://azure.microsoft.com/en-us/pricing/calculator/) before committing budget. Actual costs depend on region, reserved instances, and usage.

| Resource | Monthly Cost (USD) | Note |
|----------|-------------------|------|
| Front Door Premium (base) | ~$330 | Plus per-request fees (~$0.01 per 100K requests) |
| App Gateway v2 WAF_v2 (capacity 2) | ~$200 | Includes compute hours and GB processed |
| ACR Premium (1 month storage + scanning) | ~$650 | Scanning adds ~$0.10 per image scan; high cost item to revisit |
| Postgres Flexible Server (Burstable B2s, 32 GB storage) | ~$30 | Dev SKU; production is 5-10x higher |
| Log Analytics workspace | ~$30-100 | Depends on ingestion volume; estimate 10-30 GB/month in dev |
| Event Hub Standard (1 TU, 24-hour retention) | ~$20 | Includes 1 GB ingress/egress per month |
| Key Vault | ~$0.03/10k operations | Negligible; typically < $1/month |
| Storage (LRS, test data only) | ~$1-5 | Minimal; raw storage is cheap, egress is not |
| ACA Managed Environment | ~$0 | Infrastructure is free; consumption billed per replica-minute |
| Shim + Control Plane ACA apps (Consumption, 2 replicas each during dev) | ~$10-50 | Varies; light traffic only |
| **Rough floor (idle, no traffic)** | **~$1,200** | Running 24/7. **Spin down immediately after testing.** |

**Action items:**
- ACR Premium is the largest single cost at ~$650/month; this is flagged for Phase 2 review (may switch to Premium only in prod).
- Always tear down dev when not actively testing.

## Spin-Up: The Happy Path

Ensure you are logged into Azure with appropriate subscription permissions.

```bash
# Check you are in the right subscription
az account show --query name -o tsv

# If not, switch:
az account set --subscription <DEV_SUBSCRIPTION_ID>

# Ensure az is up to date
az version

# Deploy the infrastructure
az deployment sub create \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam

# Expected runtime: 30-45 minutes (Front Door and App Gateway take time)
# Capture the outputs, especially keyVaultUri and appGatewayPublicFqdn
```

**Prerequisites:**
- Bootstrap Key Vault is already populated with `pg-admin-password-dev` and `appgw-cert-dev` (see `docs/runbooks/bootstrap.md`).
- GitHub Environments are configured with dev secrets (see bootstrap.md section 6).
- You have a dev subscription ID.

## Spin-Down: The "Off Switch"

Use the provided teardown script. This removes all billable resources.

```bash
# Interactive mode (confirms before deletion)
scripts/teardown.sh dev

# Non-interactive (deletes immediately — use with caution)
CONFIRM=yes scripts/teardown.sh dev

# Expected runtime: 10-20 minutes
# Front Door and App Gateway deletion is slow
```

**What teardown does:**
- Deletes `rg-rac-dev` (all Tier 2 platform resources)
- Deletes `rg-rac-tier3-dev` (Tier 3 researcher app resource group, if it exists)
- Purges soft-deleted Key Vaults (dev has `kvEnablePurgeProtection=false`)
- Lists soft-deleted storage accounts (names reserved for 30 days; cannot purge)

**Important:** After teardown, confirm zero resources remain:
```bash
az resource list --subscription <DEV_SUBSCRIPTION_ID> \
  --query "[?starts_with(resourceGroup, 'rg-rac-')]" \
  -o table

# Should return empty
```

## Verifying Zero Spend

After teardown, confirm that all billable resources are gone.

### Method 1: Resource List (Immediate)

```bash
az resource list \
  --subscription <DEV_SUBSCRIPTION_ID> \
  --query "[?starts_with(resourceGroup, 'rg-rac-')]" \
  -o table
```

Should return nothing. If resources appear, manually delete them.

### Method 2: Cost Analysis (Next Day)

Cost analysis shows yesterday's and today's charges. Check the Azure Portal:

1. Cost Management + Billing → Cost Analysis
2. Filter by resource group (e.g., `rg-rac-dev`)
3. Verify the date range shows no charges after teardown

Or use the CLI (note: may require the `consumption` extension):

```bash
# Install consumption extension if needed
az extension add --name consumption

# Query usage
az consumption usage list \
  --start-date 2026-04-23 \
  --end-date 2026-04-24 \
  --query "[?contains(instanceName, 'rac-dev')]" \
  -o table
```

## Cost-Aware Parameter Overrides in dev.bicepparam

The dev parameter file is already tuned for minimal cost. Here's what's set:

```bicep
# Postgres sizing: minimal
pgSkuName = 'Standard_B2s'          # Burstable (cheaper than GeneralPurpose)
pgSkuTier = 'Burstable'
pgStorageSizeGB = 32                # Smallest practical size
pgHaMode = 'Disabled'               # No high availability (prod: ZoneRedundant)
pgBackupRetentionDays = 7           # Minimal retention (prod: 35 days)

# ACA: consumption-based, not reserved
acaZoneRedundant = false            # Single zone (prod: true)
acaProfileSku = 'Consumption'       # No dedicated capacity

# Storage: local redundancy (dev only)
# Main.bicep uses conditional: LRS for dev, GRS for staging/prod

# Key Vault: dev allows purge (cost-friendly teardown)
kvEnablePurgeProtection = false
kvSoftDeleteRetentionInDays = 7
```

**Do not override these for cost control; the values above are already minimal.**

If you want to save more in dev (not recommended), you could:
- Skip ACR Premium (use Standard instead) — saves ~$650/month but loses scanning/private linking
- Use a smaller Postgres SKU (not available; B2s is minimum)

These are strategic decisions outside the scope of this runbook.

## Iteration Loop

1. **Spin up:**
   ```bash
   az deployment sub create \
     --location eastus \
     --template-file infra/main.bicep \
     --parameters infra/environments/dev.bicepparam
   ```

2. **Verify infrastructure is ready:**
   ```bash
   az deployment sub show \
     --name rac-dev-<RUN_ID> \
     --query properties.outputs -o json
   ```

3. **Run acceptance tests:**
   - Trigger `infra-deploy` workflow (Phase 1 Task 17 documents tests)
   - Manually test endpoints if needed
   - Capture screenshots/evidence

4. **Tear down:**
   ```bash
   CONFIRM=yes scripts/teardown.sh dev
   ```

5. **Verify zero spend:**
   ```bash
   az resource list --subscription <DEV_SUBSCRIPTION_ID> \
     --query "[?starts_with(resourceGroup, 'rg-rac-')]" -o table
   ```

The entire cycle (spin up, test, spin down) takes < 2 hours total.

## Soft-Delete Caveats

### Key Vault Soft-Delete (dev only)

Dev environments have `kvEnablePurgeProtection=false`, allowing immediate hard delete on teardown. No recovery window.

For staging and prod, `kvEnablePurgeProtection=true` — soft-deleted vaults are recoverable for 90 days but occupy the namespace. If you recreate a prod Key Vault with the same name within 90 days of deletion, it will fail with "vault name already taken."

**Solution:** Wait 90 days or change the Key Vault name (update `main.bicep` parameter).

### Storage Account Soft-Delete (Account-Level Feature)

Azure Storage soft-delete is an account-level setting (enabled by default). When you delete a storage account, the name is reserved for 30 days. Attempting to create a new account with the same name will fail during this window.

**Solution:** Change `storageAccountName` in `dev.bicepparam` (e.g., bump the numeric suffix from `racdevst001` to `racdevst002`) and redeploy. After 30 days, the old name becomes available again.

**Example:**
```bicep
# Original (deleted after first iteration)
param storageAccountName = 'racdevst001'

# Next iteration (different name)
param storageAccountName = 'racdevst002'
```

## What Happens When Costs Spin Out of Control

If you forget to tear down and the environment runs idle for a week:

- **Cost:** ~$8,400 USD ($1,200/month × ~7 days)
- **Recovery:** Delete the resource groups immediately; charges stop accruing within minutes
- **Billing:** Azure bills hourly; partial hours may round up

**Prevention:**
- Set a calendar reminder to tear down environments daily
- Use automation: a scheduled workflow that tears down dev at 5 PM on weekdays
- Monitor Cost Management dashboard daily

## Further Reading

- **Azure Pricing Calculator:** https://azure.microsoft.com/en-us/pricing/calculator/
- **Cost Management + Billing:** https://docs.microsoft.com/en-us/azure/cost-management-billing/
- **Teardown script:** `/home/sysop/rac/scripts/teardown.sh`
- **Bootstrap runbook:** `docs/runbooks/bootstrap.md`
