#!/usr/bin/env bash
# Tear down a RAC environment completely. Deletes both resource groups
# and purges soft-deleted Key Vaults and Storage accounts so names can
# be reused on next deploy.
#
# Usage: scripts/teardown.sh <env>
#   env: dev | staging | prod
#
# Requires: az CLI logged in, with delete permission on target subscription.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <env>   (env: dev | staging | prod)" >&2
  exit 2
fi

ENV="$1"
case "$ENV" in
  dev|staging|prod) ;;
  *) echo "error: env must be dev|staging|prod, got '$ENV'" >&2; exit 2 ;;
esac

RG_PLATFORM="rg-rac-${ENV}"
RG_TIER3="rg-rac-tier3-${ENV}"

SUB_ID=$(az account show --query id -o tsv)
SUB_NAME=$(az account show --query name -o tsv)

echo "Teardown target:"
echo "  Subscription: $SUB_NAME ($SUB_ID)"
echo "  Environment:  $ENV"
echo "  Resource groups to delete: $RG_PLATFORM, $RG_TIER3"
echo

if [[ "${CONFIRM:-}" != "yes" ]]; then
  read -r -p "Type 'DELETE $ENV' to confirm: " CONFIRMATION
  if [[ "$CONFIRMATION" != "DELETE $ENV" ]]; then
    echo "Aborted." >&2
    exit 1
  fi
fi

delete_rg_if_exists() {
  local rg="$1"
  if az group exists --name "$rg" | grep -q true; then
    echo "Deleting resource group: $rg (async)..."
    az group delete --name "$rg" --yes --no-wait
  else
    echo "Resource group $rg does not exist — skipping."
  fi
}

delete_rg_if_exists "$RG_TIER3"
delete_rg_if_exists "$RG_PLATFORM"

echo
echo "Waiting for resource group deletions to complete (this can take 10–20 minutes)..."
for rg in "$RG_TIER3" "$RG_PLATFORM"; do
  while az group exists --name "$rg" | grep -q true; do
    sleep 15
    echo "  still deleting $rg..."
  done
  echo "  $rg deleted."
done

echo
echo "Purging soft-deleted Key Vaults whose tags match this env..."
# Key Vault purge — lists soft-deleted vaults and purges those matching our naming pattern.
SOFT_DELETED_KVS=$(az keyvault list-deleted --query "[?tags.rac_env=='${ENV}'].name" -o tsv 2>/dev/null || true)
if [[ -n "${SOFT_DELETED_KVS:-}" ]]; then
  while IFS= read -r kv; do
    [[ -z "$kv" ]] && continue
    echo "  purging KV: $kv"
    az keyvault purge --name "$kv" --no-wait || echo "  (purge failed — KV may have purge protection enabled; wait out the retention period)"
  done <<< "$SOFT_DELETED_KVS"
else
  # Fallback: purge KVs that look like ours by naming convention. We use
  # rac_env tag above; this fallback catches any that lost tags somehow.
  SOFT_DELETED_FALLBACK=$(az keyvault list-deleted --query "[?starts_with(name, 'kv-rac-${ENV}')].name" -o tsv 2>/dev/null || true)
  if [[ -n "${SOFT_DELETED_FALLBACK:-}" ]]; then
    while IFS= read -r kv; do
      [[ -z "$kv" ]] && continue
      echo "  purging KV (by name): $kv"
      az keyvault purge --name "$kv" --no-wait || echo "  (purge failed — KV may have purge protection enabled)"
    done <<< "$SOFT_DELETED_FALLBACK"
  fi
fi

echo
echo "Purging soft-deleted Storage accounts matching this env..."
# Storage account soft-delete is account-level; list and restore/delete via mgmt API.
# Note: Azure does not currently support purging a soft-deleted storage account;
# names are held for up to 30 days. Surfacing any found so the operator knows.
SOFT_DELETED_STORAGE=$(az rest --method GET \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/providers/Microsoft.Storage/deletedAccounts?api-version=2023-05-01" \
  --query "value[?contains(name, 'rac${ENV}')].name" -o tsv 2>/dev/null || true)
if [[ -n "${SOFT_DELETED_STORAGE:-}" ]]; then
  echo "  Soft-deleted storage accounts (name reserved until 30-day window expires):"
  while IFS= read -r sa; do
    [[ -z "$sa" ]] && continue
    echo "    $sa"
  done <<< "$SOFT_DELETED_STORAGE"
  echo "  Storage account purge is not supported by Azure. Either wait out the 30-day window or use a different storageAccountName in the bicepparam for the next deploy."
fi

echo
echo "Teardown complete for env=$ENV. All billable platform resources removed."
echo "Run 'scripts/cost-check.sh $ENV' (if present) to verify zero active spend."
