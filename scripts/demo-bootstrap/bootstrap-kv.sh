#!/usr/bin/env bash
# Demo bootstrap KV: creates rg-rac-bootstrap + kv-rac-bootstrap-001, seeds a
# PG admin password and a self-signed wildcard TLS cert for the given env.
#
# NOT for production. See README.md.
#
# Prereqs: setup.sh has been run (Infra Deploy SP exists with object id we
# look up by app display name).

set -euo pipefail

SUBSCRIPTION_ID="${AZ_SUBSCRIPTION_ID:-}"
ENV_NAME="${ENV_NAME:-dev}"
DOMAIN=""
LOCATION="${LOCATION:-eastus}"
BOOTSTRAP_RG="rg-rac-bootstrap"
BOOTSTRAP_KV="kv-rac-bootstrap-001"
INFRA_DEPLOY_APP_NAME="RAC Infra Deploy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscription) SUBSCRIPTION_ID="$2"; shift 2 ;;
    --env)          ENV_NAME="$2"; shift 2 ;;
    --domain)       DOMAIN="$2"; shift 2 ;;
    --location)     LOCATION="$2"; shift 2 ;;
    -h|--help)
      cat <<EOF
Usage: $0 --domain <parent-domain> [--subscription SUB_ID] [--env dev|staging|prod] [--location REGION]

--domain is the parentDomain used in infra/environments/<env>.bicepparam
(e.g., rac-dev.rac.checkwithscience.com). The TLS cert CN becomes *.<domain>.
EOF
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "ERROR: --subscription or AZ_SUBSCRIPTION_ID required" >&2
  exit 2
fi
if [[ -z "$DOMAIN" ]]; then
  echo "ERROR: --domain required (e.g., rac-dev.rac.checkwithscience.com)" >&2
  exit 2
fi

az account set --subscription "$SUBSCRIPTION_ID"

echo "==> Subscription: $SUBSCRIPTION_ID"
echo "==> Env:          $ENV_NAME"
echo "==> Domain:       $DOMAIN"
echo "==> Bootstrap RG: $BOOTSTRAP_RG / KV: $BOOTSTRAP_KV"
echo

# ---------------------------------------------------------------------------
# 1. Resource group
# ---------------------------------------------------------------------------
echo "==> Ensuring bootstrap resource group..."
if az group exists --name "$BOOTSTRAP_RG" | grep -q true; then
  echo "    [skip] $BOOTSTRAP_RG exists"
else
  az group create --name "$BOOTSTRAP_RG" --location "$LOCATION" \
    --tags purpose=rac-bootstrap rac_env=shared \
    --only-show-errors >/dev/null
  echo "    [add]  $BOOTSTRAP_RG"
fi

# ---------------------------------------------------------------------------
# 2. Key Vault
# ---------------------------------------------------------------------------
echo "==> Ensuring bootstrap Key Vault..."
if az keyvault show --name "$BOOTSTRAP_KV" --resource-group "$BOOTSTRAP_RG" --only-show-errors -o none 2>/dev/null; then
  echo "    [skip] $BOOTSTRAP_KV exists"
else
  az keyvault create \
    --resource-group "$BOOTSTRAP_RG" \
    --name "$BOOTSTRAP_KV" \
    --location "$LOCATION" \
    --enable-rbac-authorization true \
    --retention-days 7 \
    --sku standard \
    --tags purpose=rac-bootstrap rac_env=shared \
    --only-show-errors >/dev/null
  echo "    [add]  $BOOTSTRAP_KV"
fi

KV_SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${BOOTSTRAP_RG}/providers/Microsoft.KeyVault/vaults/${BOOTSTRAP_KV}"

# ---------------------------------------------------------------------------
# 3. Role grants (self + Infra Deploy SP)
# ---------------------------------------------------------------------------
echo "==> Ensuring KV role grants..."
MY_OID=$(az ad signed-in-user show --query id -o tsv)
ensure_role() {
  local role="$1" oid="$2" ptype="$3"
  local have
  have=$(az role assignment list --assignee "$oid" --scope "$KV_SCOPE" \
    --query "[?roleDefinitionName=='$role'] | length(@)" -o tsv)
  if [[ "$have" == "0" ]]; then
    az role assignment create --role "$role" \
      --assignee-object-id "$oid" \
      --assignee-principal-type "$ptype" \
      --scope "$KV_SCOPE" --only-show-errors >/dev/null
    echo "    [add]  $role → $oid"
  else
    echo "    [skip] $role → $oid"
  fi
}
ensure_role "Key Vault Administrator" "$MY_OID" "User"

SP_OID=$(az ad sp list --filter "displayName eq '$INFRA_DEPLOY_APP_NAME'" --query "[0].id" -o tsv 2>/dev/null || true)
if [[ -n "$SP_OID" ]]; then
  ensure_role "Key Vault Secrets User" "$SP_OID" "ServicePrincipal"
else
  echo "    [warn] Infra Deploy SP not found — run setup.sh first"
fi

# ---------------------------------------------------------------------------
# 4. PG admin password secret (idempotent: do not overwrite if present)
# ---------------------------------------------------------------------------
echo "==> Ensuring pg-admin-password-${ENV_NAME}..."
# Retry because RBAC can take a few seconds to propagate on first KV access
for attempt in 1 2 3 4 5 6; do
  if EXISTING=$(az keyvault secret show \
       --vault-name "$BOOTSTRAP_KV" \
       --name "pg-admin-password-${ENV_NAME}" \
       --query id -o tsv --only-show-errors 2>/dev/null); then
    echo "    [skip] secret exists ($EXISTING)"
    break
  fi
  # Not found OR 403. Distinguish by attempting a set; if 403, retry.
  PG_PASS=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
  if NEW_ID=$(az keyvault secret set \
       --vault-name "$BOOTSTRAP_KV" \
       --name "pg-admin-password-${ENV_NAME}" \
       --value "$PG_PASS" \
       --query id -o tsv --only-show-errors 2>/dev/null); then
    echo "    [add]  secret created ($NEW_ID)"
    break
  fi
  echo "    (attempt $attempt: RBAC still propagating, waiting 10s)"
  sleep 10
done

# ---------------------------------------------------------------------------
# 5. Self-signed TLS cert for *.<domain> (idempotent)
# ---------------------------------------------------------------------------
CERT_NAME="appgw-cert-${ENV_NAME}"
echo "==> Ensuring TLS certificate ${CERT_NAME}..."
EXISTING_CERT_SID=$(az keyvault certificate show \
  --vault-name "$BOOTSTRAP_KV" \
  --name "$CERT_NAME" \
  --query sid -o tsv --only-show-errors 2>/dev/null || true)
if [[ -n "$EXISTING_CERT_SID" ]]; then
  echo "    [skip] cert exists"
  CERT_SID="$EXISTING_CERT_SID"
else
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
    -days 365 \
    -subj "/CN=*.${DOMAIN}/O=RAC Demo" \
    -addext "subjectAltName = DNS:*.${DOMAIN},DNS:${DOMAIN}" \
    2>/dev/null
  PFX_PASS=$(openssl rand -base64 24)
  openssl pkcs12 -export \
    -in "$TMP/cert.pem" -inkey "$TMP/key.pem" \
    -out "$TMP/cert.pfx" \
    -passout "pass:${PFX_PASS}" \
    -name "rac-${ENV_NAME}-appgw" 2>/dev/null
  CERT_SID=$(az keyvault certificate import \
    --vault-name "$BOOTSTRAP_KV" \
    --name "$CERT_NAME" \
    --file "$TMP/cert.pfx" \
    --password "$PFX_PASS" \
    --only-show-errors \
    --query sid -o tsv)
  echo "    [add]  $CERT_SID"
fi

# ---------------------------------------------------------------------------
# 6. Emit secrets for GitHub Environment config
# ---------------------------------------------------------------------------
cat <<EOF

============================================================
 GitHub Environment '${ENV_NAME}' secrets (paste into Settings → Environments)
============================================================

  RAC_APPGW_TLS_CERT_KV_SECRET_ID = ${CERT_SID}

  RAC_PG_ADMIN_PASSWORD           = <read separately from the vault>

To fetch the PG password (DO NOT paste it here if your terminal is shared):

  az keyvault secret show \\
    --vault-name ${BOOTSTRAP_KV} \\
    --name pg-admin-password-${ENV_NAME} \\
    --query value -o tsv
============================================================
EOF
