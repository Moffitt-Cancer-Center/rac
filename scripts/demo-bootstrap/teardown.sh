#!/usr/bin/env bash
# Demo teardown: deletes the three Entra apps (cascades SP + fed creds) and
# removes the subscription-scope Owner role assignment.
# Does NOT touch deployed Azure resources; use scripts/teardown.sh for those.
set -euo pipefail

SUBSCRIPTION_ID="${AZ_SUBSCRIPTION_ID:-}"
CONFIRM="${CONFIRM:-}"

APP_NAMES=(
  "RAC Control Plane (OIDC)"
  "RAC Control Plane (API)"
  "RAC Infra Deploy"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscription) SUBSCRIPTION_ID="$2"; shift 2 ;;
    -h|--help)
      cat <<EOF
Usage: CONFIRM=yes $0 [--subscription SUB_ID]

Env fallbacks: AZ_SUBSCRIPTION_ID

Deletes the three Entra app registrations created by setup.sh and removes
the subscription-scope Owner role assignment on the Infra Deploy SP.
EOF
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "ERROR: --subscription or AZ_SUBSCRIPTION_ID required" >&2
  exit 2
fi

if [[ "$CONFIRM" != "yes" ]]; then
  echo "This will DELETE the Entra apps:" >&2
  for N in "${APP_NAMES[@]}"; do echo "  - $N" >&2; done
  echo "and the Owner role assignment on subscription $SUBSCRIPTION_ID." >&2
  echo "Re-run with CONFIRM=yes to proceed." >&2
  exit 1
fi

az account set --subscription "$SUBSCRIPTION_ID"

# Capture the Infra Deploy SP object id BEFORE deleting the app.
INFRA_APP_ID=$(az ad app list --filter "displayName eq 'RAC Infra Deploy'" --query "[0].appId" -o tsv 2>/dev/null || true)
SP_OBJECT_ID=""
if [[ -n "$INFRA_APP_ID" ]]; then
  SP_OBJECT_ID=$(az ad sp list --filter "appId eq '$INFRA_APP_ID'" --query "[0].id" -o tsv 2>/dev/null || true)
fi

# Remove the role assignment first (it'll linger as orphaned otherwise).
if [[ -n "$SP_OBJECT_ID" ]]; then
  echo "==> Removing Owner role on subscription for SP $SP_OBJECT_ID..."
  az role assignment delete \
    --assignee "$SP_OBJECT_ID" \
    --role Owner \
    --scope "/subscriptions/${SUBSCRIPTION_ID}" \
    --only-show-errors 2>/dev/null || echo "    (no assignment found — skipping)"
fi

# Delete the apps (SP + federated credentials cascade).
for NAME in "${APP_NAMES[@]}"; do
  APP_ID=$(az ad app list --filter "displayName eq '$NAME'" --query "[0].appId" -o tsv 2>/dev/null || true)
  if [[ -z "$APP_ID" ]]; then
    echo "==> [skip] '$NAME' not found"
    continue
  fi
  echo "==> Deleting '$NAME' ($APP_ID)..."
  az ad app delete --id "$APP_ID" --only-show-errors
done

echo
echo "Teardown complete."
