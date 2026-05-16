# RAC Enterprise IT Onboarding Request

**Audience:** Enterprise IT / Cloud Platform / Identity teams responsible for provisioning Azure subscriptions, Entra app registrations, DNS, and TLS certificates.

**Asker:** RAC platform owner (per-deployment institution; e.g. Moffitt Cancer Center for the first deployment).

**Purpose:** Single document enumerating the access, services, and one-time setup needed for one RAC environment (dev / staging / prod). One copy of this request per environment.

**Freshness:** 2026-05-16 (matches `infra/` Bicep modules at this revision).

---

## TL;DR

> Please provision one Azure subscription for this RAC environment, grant the deploying principal **Contributor + User Access Administrator** on it, and register the resource providers listed below. In Entra, the deploying principal needs to create app registrations, own them, and hold the Microsoft Graph `Application.ReadWrite.OwnedBy` application permission (required for federated-identity-credential creation via Bicep). One DNS subdomain must be delegated to an Azure-hosted public DNS zone in the subscription, and one CA-signed wildcard TLS certificate must be delivered as a PFX into the bootstrap Key Vault. Outbound egress from Azure to GitHub and the standard Microsoft endpoints must be permitted; inbound is only 443 to Azure Front Door.

---

## 1. Azure subscription

**One subscription per environment** (dev, staging, prod). Subscription scope is preferred over RG scope because we register resource providers and create role assignments at sub-scope.

### 1.1 Roles required for the deploying principal

| Role | Scope | Why |
|---|---|---|
| Contributor | Subscription | Deploy/manage all Azure resources |
| User Access Administrator | Subscription | Create RBAC role assignments (the Bicep creates ~12 of them per deploy) |

**Either** a service principal **or** a user account works; the runbook supports both via an env-var override. SP is preferred for CI; user account is fine for operator-driven first deploy.

### 1.2 Resource providers to register

All of these must be in `Registered` state on the subscription before first deploy:

| Resource Provider | What we deploy from it |
|---|---|
| `Microsoft.Resources` | Resource groups |
| `Microsoft.Network` | VNet, subnets, public IP, private endpoints, private DNS zones, public DNS zone, Application Gateway v2, WAF policies |
| `Microsoft.Cdn` | Azure Front Door (Premium), endpoints, origin groups, routes, custom domains, FD WAF policy |
| `Microsoft.App` | Azure Container Apps managed environment, container apps, jobs |
| `Microsoft.ContainerRegistry` | Azure Container Registry |
| `Microsoft.DBforPostgreSQL` | Postgres Flexible Server, databases, configurations |
| `Microsoft.Storage` | Storage account, blob containers, lifecycle policies, Azure Files (Tier 3 mounts) |
| `Microsoft.KeyVault` | Key Vaults (platform + pipeline + shared bootstrap) |
| `Microsoft.ManagedIdentity` | User-assigned managed identities |
| `Microsoft.OperationalInsights` | Log Analytics workspace + data exports |
| `Microsoft.Insights` | Application Insights, action groups, metric alerts, scheduled query rules, diagnostic settings |
| `Microsoft.EventHub` | Event Hub namespace + hubs (SIEM egress) |
| `Microsoft.Authorization` | Role assignments (RBAC) |

### 1.3 Resource group layout

The deploy creates / uses three resource groups; only `rg-rac-bootstrap` must exist beforehand:

| RG | Created when | Contains |
|---|---|---|
| `rg-rac-${env}` | First deploy | Tier 2 platform: VNet, KV, ACR, Postgres, ACA env, Front Door, App Gateway, Shim, Control Plane |
| `rg-rac-tier3-${env}` | First deploy | Tier 3 researcher apps (provisioned by control plane at runtime, one ACA app + Azure Files per submission) |
| `rg-rac-bootstrap` | **Pre-existing** | Shared across all envs in this subscription. Holds `kv-rac-bootstrap-001` (TLS certs, one-time secrets). |

### 1.4 Quotas / region

Region must support **Postgres Flexible Server** and have the `uuid-ossp` extension in its `azure.extensions` allowlist (universally available). `pg_uuidv7` is not on the eastus2 allowlist — we work around this.

Per-environment quota minimums:

- 1× Postgres Flexible Server (Standard_B2s for dev, Standard_D2s_v3+ for prod)
- 1× Application Gateway v2 (Standard_v2 or WAF_v2 SKU)
- 1× Front Door Premium profile
- 1× ACA Consumption workload profile (plus headroom for Tier 3 apps, ~0.25 vCPU + 0.5GiB each)
- 1× Standard SKU public IP

---

## 2. Microsoft Entra (Azure AD)

### 2.1 Deploying principal: directory permissions

| Permission | Why |
|---|---|
| Application Administrator role (or equivalent delegated rights) | Create the `rac-pipeline-${env}` app registration per deployment |
| Owner of the apps created above | Required for the Microsoft Graph Bicep extension to write child resources (federated identity credentials) — Owner relationship scopes which apps the deploying principal can write to |
| **Microsoft Graph application permission: `Application.ReadWrite.OwnedBy`** | Required in addition to Owner. Owner alone returns 403 from Graph during FIC creation. Grant via `az ad sp app-permission add` followed by direct admin-consent (the CLI's `admin-consent` subcommand silently no-ops on some tenants — runbook §3.5.2a documents the direct Graph POST that always works) |

### 2.2 Pre-existing app registrations (IT can create, hand IDs back)

Two app registrations are used for end-user OIDC sign-in. IT can pre-create these and hand the client IDs over; no special perms required afterwards.

| App reg | Redirect URI | Used by |
|---|---|---|
| `rac-control-plane-${env}` | `https://${parentDomain}/auth/callback` | Researchers / admins / reviewers signing into the control plane SPA + API |
| `rac-shim-${env}` | `https://*.${parentDomain}/_shim/auth/callback` | Cookie-mode reviewer sessions on Tier 3 researcher apps |

Both should have `User.Read` Graph delegated permission only. No app permissions.

### 2.3 Directory read for the control plane managed identity

The control plane's user-assigned managed identity needs:

- `User.Read.All` (application)
- `Group.Read.All` (application)

These power the periodic group/role sync ("graph-sweep" ACA job) and the PI lookup picker on the submission form. IT grants these on the managed identity via Microsoft Graph appRoleAssignments.

---

## 3. DNS

We host the deployment's DNS zone in Azure (`Microsoft.Network/dnsZones` in the subscription). IT needs to **delegate a subdomain** to it.

### Pattern

For environment `${env}` and corporate parent zone `${corpDomain}`:

- Deployment public domain: `rac-${env}.${corpDomain}` (e.g. `rac-dev.moffitt.org`)
- Azure creates a public DNS zone with that name in `rg-rac-${env}`
- IT adds `NS` records at `${corpDomain}` pointing `rac-${env}` at the four Azure name-servers we provide after first deploy.

Per-Tier-3-app records (`*.rac-${env}.${corpDomain}`) are written into the Azure zone by the control plane MI at runtime (it holds DNS Zone Contributor at the zone scope) — no IT involvement after delegation.

---

## 4. TLS certificates

**One CA-signed wildcard certificate per environment**, covering both the apex and wildcard:

- Subject / SAN: `rac-${env}.${corpDomain}` and `*.rac-${env}.${corpDomain}`
- Format: PFX with private key
- Delivered to: `kv-rac-bootstrap-001` in `rg-rac-bootstrap`, as a KV certificate named `rac-${env}-tls`

Both Front Door (edge TLS) and App Gateway (origin TLS) pull from this single certificate via their respective managed identities.

> **Self-signed works for App Gateway origin but causes intermittent Front Door 502s (`OriginCertificateSelfSigned`).** CA-signed is strongly preferred. For dev/test only, self-signed is tolerable as long as the cert is replaced before any user-facing testing.

Renewal: 90-day LE certs work; IT just needs to update the KV cert version. No app redeploy required (FD + AppGw poll KV).

---

## 5. Networking / firewall

### 5.1 Inbound

- **Public Internet → Azure Front Door anycast frontend on `443/TCP` only.**
- No other inbound. App Gateway is fronted by Front Door; ACA is fronted by App Gateway (internal LB). Researchers / reviewers / admins all enter via Front Door.

### 5.2 Outbound (from the ACA subnet)

The control plane, shim, jobs, and Tier 3 researcher apps need outbound egress to:

| FQDN pattern | Purpose |
|---|---|
| `*.azurecr.io` | Container image pulls (control plane, shim, researcher images) |
| `*.vaultcore.azure.net` | Key Vault (prefer private endpoint; FQDN listed in case of corp egress proxy) |
| `*.postgres.database.azure.com` | Postgres (prefer private endpoint) |
| `*.blob.core.windows.net` | Storage (prefer private endpoint) |
| `*.servicebus.windows.net` | Event Hub (SIEM egress) |
| `graph.microsoft.com`, `login.microsoftonline.com` | Entra OIDC + Graph |
| `api.github.com` | `repository_dispatch` to trigger the pipeline GHA |
| Researcher image base layers as required | Tier 3 apps; varies per submission. If egress is filtered, an exception list per researcher app may be needed |

---

## 6. GitHub (out-of-band from Azure, but IT may own org access)

A GitHub Organization (or per-team set of repos) hosting **two** repos:

| Repo | Role |
|---|---|
| `rac` (this repo) | Control plane, shim, infra Bicep, runbooks |
| `rac-pipeline` (separate sibling) | Pipeline GHA workflows that build researcher images. **Must live in a separate repo so researcher Dockerfiles never execute against the main repo's code.** |

**Per environment, on `rac-pipeline`:**

- A GitHub **Environment** named `${env}` (e.g. `dev`)
- Environment variables: `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`, `KV_NAME` (pipeline KV), `ACR_NAME`
- OIDC federation is configured Azure-side via the federated identity credential on the `rac-pipeline-${env}` Entra app reg (subject `repo:${owner}/rac-pipeline:environment:${env}`). No GitHub-side admin needed beyond environment creation.

---

## 7. Cost expectations (rough, dev posture)

For sizing approval conversations. Real numbers depend on region + reservations.

| Component | Approx daily cost (dev, Burstable PG, no traffic) |
|---|---|
| Application Gateway v2 | $25–$30/day (largest single line item) |
| Postgres Flexible Server (Standard_B2s) | ~$1.50/day |
| Azure Container Registry (Standard) | ~$0.55/day |
| ACA Consumption profile (idle) | < $0.20/day |
| Storage (RA-GRS, < 10 GB) | pennies |
| Key Vaults (3× Standard) | pennies |
| Log Analytics / App Insights (light) | pennies |
| Front Door Premium (base) | included in Premium SKU monthly |

Prod sizing scales Postgres, ACA workload profile, and per-Tier-3-app costs proportionally to researcher usage.

---

## 8. Checklist for IT

Tear-off summary for the IT ticket:

- [ ] Subscription provisioned, deploying principal granted **Contributor + User Access Administrator** at sub-scope
- [ ] Resource providers registered (see §1.2)
- [ ] `rg-rac-bootstrap` RG created in the subscription
- [ ] Deploying principal can create app registrations in Entra, and is Owner of any it creates
- [ ] Deploying principal granted **Microsoft Graph: `Application.ReadWrite.OwnedBy`** (application permission, admin-consented)
- [ ] Two end-user OIDC app registrations created and client IDs delivered (§2.2)
- [ ] Control plane managed identity (created at first deploy) granted **Graph: `User.Read.All` + `Group.Read.All`** (application, admin-consented). IT may need to grant these post-first-deploy once the MI exists.
- [ ] DNS subdomain `rac-${env}.${corpDomain}` delegated to Azure name-servers (provided after first deploy)
- [ ] CA-signed wildcard PFX delivered to `kv-rac-bootstrap-001` as cert `rac-${env}-tls`
- [ ] Outbound egress allow-list updated (§5.2) if corporate firewall is in path
- [ ] GitHub Organization access confirmed for `rac` and `rac-pipeline` repos

---

## Related docs

- [`docs/runbooks/bootstrap.md`](bootstrap.md) — Full first-deploy walkthrough (operator-facing; references this doc as a prerequisite)
- [`docs/design-plans/2026-04-23-rac-v1.md`](../design-plans/2026-04-23-rac-v1.md) — Architecture rationale
- [`infra/CLAUDE.md`](../../infra/CLAUDE.md) — Bicep module map + two-pass deploy gotchas
