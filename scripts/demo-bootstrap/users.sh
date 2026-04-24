#!/usr/bin/env bash
# Demo users: create or verify an Entra member user in the current tenant.
# Intended for test-driving RAC sign-in flows after a demo deploy.
#
# NOT for production — real Moffitt users should be provisioned through
# normal institutional IdP processes.
set -euo pipefail

UPN=""
DISPLAY_NAME=""
PASSWORD=""
FORCE_CHANGE=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --upn)             UPN="$2"; shift 2 ;;
    --display-name)    DISPLAY_NAME="$2"; shift 2 ;;
    --password)        PASSWORD="$2"; shift 2 ;;
    --no-force-change) FORCE_CHANGE=false; shift ;;
    -h|--help)
      cat <<EOF
Usage: $0 --upn <user-principal-name> [--display-name <name>] [--password <pw>] [--no-force-change]

--upn must be <localpart>@<verified-tenant-domain>
   (e.g., testresearcher@jdeangelisoutlook630.onmicrosoft.com).

If --password is omitted, a random 20-char password meeting Entra complexity
is generated and printed once. If --display-name is omitted, it is derived
from the UPN local-part.

By default the user must change password on first sign-in. Pass
--no-force-change to skip that (useful for automated test accounts).
EOF
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$UPN" ]]; then
  echo "ERROR: --upn required" >&2
  exit 2
fi
if [[ "$UPN" != *"@"* ]]; then
  echo "ERROR: --upn must be in <local>@<domain> form" >&2
  exit 2
fi

MAIL_NICK="${UPN%@*}"
DOMAIN="${UPN#*@}"

if [[ -z "$DISPLAY_NAME" ]]; then
  # Turn "john.doe" or "john_doe" into "john doe", title-case via sed.
  DISPLAY_NAME=$(echo "$MAIL_NICK" | tr '._-' ' ')
fi

TENANT_ID=$(az account show --query tenantId -o tsv)
echo "==> Tenant:        $TENANT_ID"
echo "==> UPN:           $UPN"
echo "==> Display name:  $DISPLAY_NAME"

# Verify the domain component is one of the tenant's verified domains;
# otherwise 'az ad user create' fails with a confusing 'property verifiedDomains' error.
if ! az rest --method GET --url "https://graph.microsoft.com/v1.0/domains" \
     --query "value[?id=='${DOMAIN}'].id" -o tsv 2>/dev/null | grep -qx "$DOMAIN"; then
  echo "ERROR: '$DOMAIN' is not a verified domain on this tenant." >&2
  echo "       Verified domains on this tenant:" >&2
  az rest --method GET --url "https://graph.microsoft.com/v1.0/domains" \
     --query "value[].id" -o tsv 2>/dev/null | sed 's/^/         /' >&2
  exit 3
fi

# Idempotent: if the user already exists, report and exit.
EXISTING=$(az ad user list --filter "userPrincipalName eq '$UPN'" --query "[0].{id:id, displayName:displayName}" -o json 2>/dev/null || echo "")
if [[ -n "$EXISTING" && "$EXISTING" != "null" && "$EXISTING" != "{}" ]]; then
  echo
  echo "    [skip] user already exists:"
  echo "    $EXISTING"
  exit 0
fi

# Generate a password if one wasn't provided. The prefix "Ax9!" guarantees
# the result meets Entra's 3-of-4 classes rule (upper/lower/digit/symbol).
PW_GENERATED=0
if [[ -z "$PASSWORD" ]]; then
  RANDOM_PART=$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)
  PASSWORD="Ax9!${RANDOM_PART}"
  PW_GENERATED=1
fi

az ad user create \
  --display-name "$DISPLAY_NAME" \
  --user-principal-name "$UPN" \
  --password "$PASSWORD" \
  --force-change-password-next-sign-in "$FORCE_CHANGE" \
  --mail-nickname "$MAIL_NICK" \
  --only-show-errors \
  --query "{upn:userPrincipalName, id:id, displayName:displayName}" -o json

if [[ "$PW_GENERATED" == "1" ]]; then
  cat <<EOF

============================================================
 Generated credentials (save now — won't be shown again)
============================================================
  UPN:      $UPN
  Password: $PASSWORD
============================================================
EOF
else
  echo "    Password set to the value you provided."
fi

if [[ "$FORCE_CHANGE" == "true" ]]; then
  echo "    First sign-in will force a password change."
fi
