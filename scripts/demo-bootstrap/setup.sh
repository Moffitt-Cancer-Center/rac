#!/usr/bin/env bash
# Demo bootstrap: creates Entra apps, SP, federated credentials, role assignments.
# NOT for production. See README.md.
set -euo pipefail

SUBSCRIPTION_ID="${AZ_SUBSCRIPTION_ID:-}"
GITHUB_REPO="${GITHUB_REPO:-Moffitt-Cancer-Center/rac}"
ENVIRONMENTS=(dev staging prod)

APP_OIDC_NAME="RAC Control Plane (OIDC)"
APP_API_NAME="RAC Control Plane (API)"
APP_DEPLOY_NAME="RAC Infra Deploy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscription) SUBSCRIPTION_ID="$2"; shift 2 ;;
    --github-repo) GITHUB_REPO="$2"; shift 2 ;;
    -h|--help)
      cat <<EOF
Usage: $0 [--subscription SUB_ID] [--github-repo ORG/REPO]

Env fallbacks: AZ_SUBSCRIPTION_ID, GITHUB_REPO
EOF
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "ERROR: --subscription or AZ_SUBSCRIPTION_ID required" >&2
  exit 2
fi

az account set --subscription "$SUBSCRIPTION_ID"
TENANT_ID=$(az account show --query tenantId -o tsv)

echo "==> Subscription: $SUBSCRIPTION_ID"
echo "==> Tenant:       $TENANT_ID"
echo "==> GitHub repo:  $GITHUB_REPO"
echo

# ---------------------------------------------------------------------------
# 1. Register resource providers (idempotent; async)
# ---------------------------------------------------------------------------
echo "==> Registering resource providers..."
PROVIDERS=(
  Microsoft.App
  Microsoft.ContainerRegistry
  Microsoft.DBforPostgreSQL
  Microsoft.KeyVault
  Microsoft.Storage
  Microsoft.OperationalInsights
  Microsoft.Insights
  Microsoft.Cdn
  Microsoft.Network
  Microsoft.Security
)
for NS in "${PROVIDERS[@]}"; do
  az provider register --namespace "$NS" --only-show-errors >/dev/null &
done
wait
echo "    (registration is async; check with 'az provider list')"
echo

# ---------------------------------------------------------------------------
# 2. Create or find the three Entra apps
# ---------------------------------------------------------------------------
ensure_app() {
  local name="$1"
  local extra_args="${2:-}"
  local existing
  existing=$(az ad app list --filter "displayName eq '$name'" --query "[0].appId" -o tsv 2>/dev/null || true)
  if [[ -n "$existing" ]]; then
    echo "$existing"
    return
  fi
  # shellcheck disable=SC2086
  az ad app create \
    --display-name "$name" \
    --sign-in-audience AzureADMyOrg \
    $extra_args \
    --only-show-errors \
    --query appId -o tsv
}

echo "==> Ensuring Entra apps..."
APP_OIDC_ID=$(ensure_app "$APP_OIDC_NAME" "--public-client-redirect-uris http://localhost:3000/callback")
echo "    OIDC:   $APP_OIDC_ID"
APP_API_ID=$(ensure_app "$APP_API_NAME" "")
echo "    API:    $APP_API_ID"
APP_DEPLOY_ID=$(ensure_app "$APP_DEPLOY_NAME" "")
echo "    Deploy: $APP_DEPLOY_ID"
echo

# ---------------------------------------------------------------------------
# 3. Ensure service principal for the Infra Deploy app
# ---------------------------------------------------------------------------
echo "==> Ensuring service principal for Infra Deploy app..."
SP_OBJECT_ID=$(az ad sp list --filter "appId eq '$APP_DEPLOY_ID'" --query "[0].id" -o tsv 2>/dev/null || true)
if [[ -z "$SP_OBJECT_ID" ]]; then
  SP_OBJECT_ID=$(az ad sp create --id "$APP_DEPLOY_ID" --only-show-errors --query id -o tsv)
fi
echo "    SP object id: $SP_OBJECT_ID"
echo

# ---------------------------------------------------------------------------
# 4. Federated credentials for each GitHub Environment
# ---------------------------------------------------------------------------
echo "==> Ensuring federated credentials..."
EXISTING_FC=$(az ad app federated-credential list --id "$APP_DEPLOY_ID" --query "[].name" -o tsv 2>/dev/null || true)
for ENV_NAME in "${ENVIRONMENTS[@]}"; do
  FC_NAME="rac-env-${ENV_NAME}"
  if echo "$EXISTING_FC" | grep -qx "$FC_NAME"; then
    echo "    [skip] $FC_NAME already present"
    continue
  fi
  az ad app federated-credential create \
    --id "$APP_DEPLOY_ID" \
    --parameters "{\"name\":\"${FC_NAME}\",\"issuer\":\"https://token.actions.githubusercontent.com\",\"subject\":\"repo:${GITHUB_REPO}:environment:${ENV_NAME}\",\"audiences\":[\"api://AzureADTokenExchange\"]}" \
    --only-show-errors >/dev/null
  echo "    [add]  $FC_NAME"
done
echo

# ---------------------------------------------------------------------------
# 4b. Enable Defender for Containers (subscription-scope)
# ---------------------------------------------------------------------------
echo "==> Ensuring Defender for Containers on Standard tier..."
DEFENDER_TIER=$(az security pricing show --name Containers --query pricingTier -o tsv 2>/dev/null || echo "")
if [[ "$DEFENDER_TIER" == "Standard" ]]; then
  echo "    [skip] already Standard"
else
  az security pricing create --name Containers --tier Standard --only-show-errors >/dev/null
  echo "    [set]  Standard"
fi
echo

# ---------------------------------------------------------------------------
# 5. Grant Owner on the subscription
# ---------------------------------------------------------------------------
echo "==> Ensuring Owner role on subscription..."
SCOPE="/subscriptions/${SUBSCRIPTION_ID}"
HAS_OWNER=$(az role assignment list \
  --assignee "$SP_OBJECT_ID" \
  --scope "$SCOPE" \
  --query "[?roleDefinitionName=='Owner'] | length(@)" -o tsv)
if [[ "$HAS_OWNER" == "0" ]]; then
  az role assignment create \
    --role Owner \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --scope "$SCOPE" \
    --only-show-errors >/dev/null
  echo "    [add]  Owner on $SCOPE"
else
  echo "    [skip] Owner already present on $SCOPE"
fi
echo

# ---------------------------------------------------------------------------
# 6. Emit GitHub Environment secrets
# ---------------------------------------------------------------------------
cat <<EOF
============================================================
 GitHub Environment secrets (paste into Settings → Environments)
============================================================

For each of dev / staging / prod:
  AZURE_CLIENT_ID        = $APP_DEPLOY_ID
  AZURE_TENANT_ID        = $TENANT_ID
  AZURE_SUBSCRIPTION_ID_<ENV>  = $SUBSCRIPTION_ID

Frontend / backend app registrations (for future frontend wiring):
  OIDC app client ID     = $APP_OIDC_ID
  API  app client ID     = $APP_API_ID

Service principal object id (reference only):
  $SP_OBJECT_ID
============================================================
EOF
