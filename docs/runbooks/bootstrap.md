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
az keyvault set-policy \
  --name kv-rac-bootstrap-001 \
  --object-id <SERVICE_PRINCIPAL_OBJECT_ID> \
  --secret-permissions get list
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

## 6. GitHub Environments and Secrets

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

### TLS Certificate Setup

If using App Gateway with a Key Vault-referenced certificate:

1. The certificate is already stored in the bootstrap Key Vault.
2. Grant App Gateway's managed identity `Key Vault Certificates User` on the bootstrap vault:

```bash
# Get App Gateway's managed identity
APP_GW_ID=$(az resource show \
  --resource-group rg-rac-dev \
  --name appgw-rac-dev \
  --resource-type "Microsoft.Network/applicationGateways" \
  --query identity.principalId -o tsv)

# Grant access
az keyvault set-policy \
  --name kv-rac-bootstrap-001 \
  --object-id $APP_GW_ID \
  --certificate-permissions get list
```

### Defender for Containers Verification

```bash
# Confirm Defender is enabled
az security pricing list --query "[?name == 'Containers']" -o table

# After the first container image is pushed to ACR (Phase 3), 
# Defender will scan it. Monitor in the Azure Portal:
# Container Registry → Security → Vulnerabilities
```

## 9. Idempotency Check

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

## Next Steps

1. Confirm all Tier 2 infrastructure is operational (Task 1 acceptance checks).
2. Proceed to Phase 2: Control Plane skeleton (auth, submission schema, CRUD operations).
3. After Phase 2, run `infra-deploy` a second time to wire up Control Plane identity permissions (re-deploy loop in Task 12B).
