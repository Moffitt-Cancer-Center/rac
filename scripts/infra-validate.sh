#!/usr/bin/env bash
# Pre-deploy validation for RAC infra Bicep IaC.
#
# Catches at compile time: syntax errors, type errors, BCP warnings.
# Catches at ARM-validation time: param-shape errors, region offer
# restrictions, explicit-false rejections, name collisions, role-scope errors.
#
# Run before pushing any change under infra/. Validation cycle is ~30s vs
# ~25min for a full deploy + teardown cycle.

set -euo pipefail

cd "$(dirname "$0")/.."

ENV_NAME="${1:-dev}"
case "$ENV_NAME" in
  dev|staging|prod) ;;
  *) echo "Usage: $0 [dev|staging|prod]" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# 1. Bicep compile + warnings-as-errors
# ---------------------------------------------------------------------------
echo "==> Compiling Bicep (modules + main, warnings treated as errors)..."
HAD_ISSUE=0
for f in infra/main.bicep infra/modules/*.bicep; do
  OUT=$(az bicep build --file "$f" 2>&1 || true)
  ISSUES=$(echo "$OUT" | grep -E '\b(Error|Warning) BCP[0-9]+\b' || true)
  if [[ -n "$ISSUES" ]]; then
    echo "  ✗ $f"
    echo "$ISSUES" | sed 's/^/      /'
    HAD_ISSUE=1
  fi
done
if [[ $HAD_ISSUE -ne 0 ]]; then
  echo
  echo "Bicep compile failed (warnings or errors). Fix the issues above before deploying." >&2
  exit 1
fi
echo "  ✓ All modules compiled clean."
echo

# ---------------------------------------------------------------------------
# 2. ARM validation against the target environment
# ---------------------------------------------------------------------------
PARAM_FILE="infra/environments/${ENV_NAME}.bicepparam"
LOCATION=$(grep "^param location" "$PARAM_FILE" | sed -E "s/.*= ?'([^']+)'.*/\1/")
LOCATION="${LOCATION:-eastus2}"

# bicepparam reads these via readEnvironmentVariable() — provide either real
# values from the bootstrap KV or harmless placeholders.
case "$ENV_NAME" in
  dev)
    BOOTSTRAP_KV="kv-rac-bootstrap-001"
    PG_PASS=$(az keyvault secret show --vault-name "$BOOTSTRAP_KV" --name pg-admin-password-dev --query value -o tsv 2>/dev/null || echo "")
    CERT_SID=$(az keyvault certificate show --vault-name "$BOOTSTRAP_KV" --name appgw-cert-dev --query sid -o tsv 2>/dev/null || echo "")
    ;;
  *)
    PG_PASS=""
    CERT_SID=""
    ;;
esac

# Placeholders for envs / cases without a real bootstrap KV. These pass
# Bicep's @secure / string validation but won't satisfy ARM if the deploy
# actually runs — that's fine, validate doesn't deploy.
PG_PASS="${PG_PASS:-Validate-Placeholder-Pw1!}"
CERT_SID="${CERT_SID:-https://placeholder.vault.azure.net/secrets/appgw-cert-${ENV_NAME}/00000000000000000000000000000000}"

echo "==> az deployment sub validate (env=$ENV_NAME, location=$LOCATION)..."
RESULT=$(RAC_PG_ADMIN_PASSWORD="$PG_PASS" RAC_APPGW_TLS_CERT_KV_SECRET_ID="$CERT_SID" \
  az deployment sub validate \
    --location "$LOCATION" \
    --template-file infra/main.bicep \
    --parameters "$PARAM_FILE" \
    --query "{state:properties.provisioningState, err:properties.error}" \
    -o json 2>&1 || true)

if echo "$RESULT" | grep -qE '"state": *"Succeeded"'; then
  echo "  ✓ ARM validation passed."
else
  echo "  ✗ ARM validation failed:"
  echo "$RESULT" | sed 's/^/      /'
  exit 1
fi
echo
echo "All validations passed for env=$ENV_NAME."
