# Demo Bootstrap

**This is NOT part of the RAC platform.** These scripts exist to stand up a
throwaway demo deploy of RAC against a personal Azure subscription and a
personal Entra tenant — the kind of setup a developer uses to exercise the
bootstrap runbook end-to-end without touching institutional infrastructure.

A real institutional deploy (e.g., Moffitt production) should NOT run these
scripts. Production operators should instead follow [`docs/runbooks/bootstrap.md`](../../docs/runbooks/bootstrap.md)
manually, because:

- The display names below (`RAC Control Plane (OIDC)` etc.) collide with any
  prior deploy in the same tenant.
- `setup.sh` grants `Owner` on the entire subscription to the GHA deploy SP.
  In a shared org that's overprivileged; real deploys scope to the target
  resource groups only.
- The TLS cert step generates a **self-signed** cert. Fine for a personal demo
  domain; not acceptable for anything real.

## What setup.sh does

1. Registers the 10 Azure resource providers required by `infra/main.bicep`.
2. Creates three Entra app registrations:
   - `RAC Control Plane (OIDC)` — researcher sign-in
   - `RAC Control Plane (API)` — protected resource
   - `RAC Infra Deploy` — GHA deploy service principal
3. Creates the service principal for `RAC Infra Deploy`.
4. Adds federated credentials for `repo:<github-repo>:environment:{dev,staging,prod}`.
5. Grants the SP `Owner` on the target subscription.
6. Emits a block of `KEY=VALUE` lines ready to paste into GitHub Environment
   secrets.

Each step is idempotent: re-running on an already-bootstrapped subscription
picks up the existing apps/SP/creds/role by name rather than failing.

## What teardown.sh does

Deletes the three apps (which cascades to SP + federated credentials) and
removes the subscription-scope Owner role assignment. It does NOT touch any
deployed Azure resources — use `scripts/teardown.sh` for those.

## Usage

```bash
# Log in first (once per shell):
az login
az account set --subscription <SUBSCRIPTION_ID>

# Run the bootstrap (idempotent)
./scripts/demo-bootstrap/setup.sh \
  --subscription <SUBSCRIPTION_ID> \
  --github-repo Moffitt-Cancer-Center/rac

# When done with the demo
./scripts/demo-bootstrap/teardown.sh \
  --subscription <SUBSCRIPTION_ID>
```

Both scripts read `AZ_SUBSCRIPTION_ID` and `GITHUB_REPO` from the environment
as fallbacks if the flags are omitted.

## What this does NOT cover

- Bootstrap Key Vault (`kv-rac-bootstrap-001`) + PG admin passwords — covered
  by the bootstrap runbook Section 4. Needs the target parent DNS domain
  picked first.
- Self-signed TLS certs for App Gateway — depends on parent domain.
- DNS zone delegation — requires NS-record authority at the parent.
- GitHub Environments UI configuration — still manual at
  `github.com/<org>/<repo>/settings/environments`.

These are the steps that most demand human judgement (domain choice,
parent-zone authority, reviewer-gate policy) and shouldn't be automated blind.
