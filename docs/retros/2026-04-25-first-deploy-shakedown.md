# First-deploy shakedown — 2026-04-25

End-to-end deploy of the RAC v1 platform to a fresh Azure subscription
(personal trial sub `44370559…`, region `eastus2`, tenant
`jdeangelisoutlook630.onmicrosoft.com`). Took **7 deploy attempts** over
~5 hours of wall time to land all 16 modules cleanly.

This retro captures what we learned so the next deploy (Moffitt prod, or a
second demo on a different sub) doesn't re-pay the same costs.

## Outcome

- 16/16 modules deployed; 39 resources in `rg-rac-dev`; pass-1 baseline ready
  for the Phase 5 bridge re-deploy + Phase 2 / Phase 6 image deploys.
- Source committed to `main` through commit `99d3031`. The full chain of
  fixes is in commits `3294fa8`…`99d3031`.

## What broke and why

The Bicep was authored module-by-module from the implementation plan but
**never deployed end-to-end** before this session. Each module compiled and
validated against its own scope, but cross-module assumptions and ARM-side
runtime constraints surfaced one at a time:

| Issue | Surface | Detection cost |
|---|---|---|
| `enablePurgeProtection: false` on KV | ARM apply | 1 deploy cycle |
| Hard-coded globally-unique names (`pgServerName=rac-dev-pg`) collide across subscriptions | ARM apply | 1 deploy cycle |
| `pg_uuidv7` not in `azure.extensions` allowlist for region | ARM apply | 1 deploy cycle |
| ACR private-DNS zone group references zone declared after it | ARM apply (race) | 1 deploy cycle |
| Storage account child resources race the parent | ARM apply (race) | 1 deploy cycle |
| App Gateway HTTPS listener has both `hostName` and `hostNames` | ARM validation | 1 deploy cycle |
| Front Door WAF policy missing `sku.name` for Premium profile | ARM validation | 1 deploy cycle |
| Front Door WAF policy name has hyphens (rejected; AppGw WAF allows them) | ARM validation | 1 deploy cycle |
| Front Door `Microsoft_DefaultRuleSet` 2.x requires per-rule actions | ARM validation | 1 deploy cycle |
| Pipeline-stuck alert references custom table that doesn't exist on first deploy | ARM validation | 1 deploy cycle |
| Bicep `'''…'''` strings don't interpolate `${…}` | ARM validation (KQL parse) | 1 deploy cycle |
| BCP318 warnings predicted real bugs and were ignored | n/a | latent until hit |
| GHA OIDC 5-min assertion vs 60-min deploy = `AADSTS700024` | GHA poll | 1 deploy cycle |
| Region offer restrictions for PG on personal subs (eastus → eastus2) | ARM apply | 1 deploy cycle |
| App Gateway MI has no access to the bootstrap KV (cross-RG) | ARM apply (latent) | caught in audit |
| Front Door wildcard `ManagedCertificate` (not supported) | latent | caught in audit |

## What helped

- **`scripts/infra-validate.sh`** — runs `az bicep build` + `az deployment
  sub validate` in ~30s. Treats BCP warnings as errors. Catches everything
  in the "ARM validation" rows above without spending a deploy cycle.
  Massively shortened iteration time after the audit.
- **An Opus-class audit subagent reading every module** before applying
  fixes. Cheap on calendar time; surfaced bugs ahead of deploy that would
  have cost 3-4 more iterations. Better than continuing to whack moles.
- **Explicit `dependsOn`** on cross-resource references where Bicep's
  inference is technically correct but ARM's parallel scheduler doesn't
  honor it tightly enough.
- **`--no-wait` + polling** in the GHA workflow, so OIDC token expiry
  doesn't kill long deploys.

## What we'd do differently next time

1. **Run `scripts/infra-validate.sh` in CI on every PR touching infra/**.
   Block merges on BCP warnings or validate failures. This catches ~80% of
   the bugs above before they reach a deploy.
2. **Per-module sandbox deploys** for the modules that surfaced runtime
   ARM-side bugs (ACR DNS group, blob storage children, FD WAF). A
   `scripts/infra-test-module.sh <module>` that deploys one module to a
   throwaway RG with stub params is ~3-5 min per run vs 25 min for full
   teardown. Worth building before next major Bicep change.
3. **Region pre-flight**: probe each resource type's offer availability
   before committing to a region. PG flexible-server allowlists vary; ACA
   first-deploy times vary. Add to demo-bootstrap.
4. **Bake the deploy-once-and-update flow into the runbook**: ARM is
   idempotent on resources; partial failures don't always need full
   teardown. Document when to teardown vs re-run.

## Active follow-ups

- Phase 5 bridge re-deploy with `controlPlaneIdentityPrincipalId=
  26cd8ecb-3f3b-41b1-b4e7-65fdca492d5b` to wire the DNS Zone Contributor
  role on `rac-dev.rac.checkwithscience.com`.
- Phase 2 (Control Plane app) Docker image build + push to ACR + first
  deploy. Also update Alembic migration 0001 to use `uuid_generate_v4()`
  (uuid-ossp) instead of `uuid_generate_v7()` (pg_uuidv7-only) since
  eastus2 doesn't have pg_uuidv7 on the allowlist.
- Phase 6 (Shim) image build + deploy. After this, flip
  `deployCustomDomain=true` on Front Door once the cert path is wired
  with `CustomerCertificate` referencing the bootstrap KV.
- `rac-pipeline` repo: not yet pushed to GitHub; needs its own repo + OIDC
  federated credentials.
- Add `scripts/infra-test-module.sh` for per-module sandbox testing.
- Consider adding `infra-validate.sh` to GHA pre-push.

## Addendum — 2026-04-26 (Phase 2 + Phase 6 image deploy)

Same flavor of bugs (Bicep auth/permission/network gaps + a couple of
runtime app-side surprises). Pass-1 platform was untouched; Phase 2 control
plane and Phase 6 shim are now both live with `/health` and
`/_shim/health` returning 200. Image tag `dev-003`, ACR
`racdevacrczo2xbgcnq.azurecr.io`. All 12 alembic migrations applied via
`az containerapp exec`.

### Permission/role gaps

- Control plane + shim user-assigned MIs were missing **Key Vault Secrets
  User** on the platform KV. ACA's `secretref` to `rac-pg-admin-password`
  / `shim-database-dsn` / `shim-cookie-hmac` failed at startup with
  generic "secret not found". Fixed in `role-assignments.bicep`.
- Same MIs were missing **AcrPull** on the platform ACR. Image pull
  failed silently (revision provisioning stalled). Fixed in `acr.bicep`
  by adding two scoped role assignments.
- Control-plane MI ended up with **Contributor on `rg-rac-dev`** as a
  side-effect of how `role-assignments.bicep` is currently wired. Over-
  grant; flagged as a follow-up.

### Bicep / ACR gotchas

- `acr.bicep` had `quarantinePolicy` enabled by default. With no
  scanner-releaser attached, every pushed tag came back as
  `MANIFEST_UNKNOWN` on pull. Fixed by setting `status: 'disabled'`.
- The application database (`rac`) was being created out-of-band; moved
  into `postgres.bicep` as a child `appDatabase` resource so it's
  declarative.
- Three KQL alerts referenced Log Analytics columns/tables that don't
  exist on a fresh workspace (`ContainerAppConsoleLogs_CL.StatusCode_d`,
  `RAC_PipelineLog_CL`). ARM rejected the deploy with `InvalidQuery`.
  Fixed by gating them behind `deployTelemetryAlerts bool = false`.

### Networking

- Both ACR and platform KV ship with `publicNetworkAccess: 'Disabled'`.
  First-time `docker push` from a laptop and `az keyvault secret set`
  for operator-managed secrets both fail until you temporarily flip
  public access on. Documented as a runbook pattern; long-term answer is
  a VNet-resident CI runner.

### App-side surprises

- Control plane `logging_setup.py` had `structlog.stdlib.add_logger_name`
  in the chain. Incompatible with `PrintLoggerFactory`; first WARN emit
  killed the worker. Removed.
- `migrations/env.py` was treating the literal `driver://...` placeholder
  in `alembic.ini` as a usable URL, so an interactive `alembic` invocation
  in the container tried to connect to localhost. Patched to fall back to
  `Settings()` when the URL is the placeholder.
- Shim Dockerfile was invoking `gunicorn` directly. The uv-built venv
  shebang has an absolute path to the build-stage `/app/.venv` that
  doesn't survive `COPY --from=builder` into the runtime stage; it
  failed with `exec format error`. Switched to `python -m gunicorn`.
- Shim was missing `aiohttp>=3.10` as a direct dep; `azure-identity`'s
  async credential flow needs it. Added to `pyproject.toml`.
- Migration 0001 originally used `pg_uuidv7`, which isn't on the
  `azure.extensions` allowlist for `eastus2`. Swapped to `uuid-ossp` +
  a `uuidv7()` SQL wrapper that returns `uuid_generate_v4()`. Lose
  v7 time-ordering, gain portability.

### Active follow-ups (carried forward)

- Switch control-plane DB user from `rac_admin` to `rac_app` with its
  own KV-stored password (today's smoke-test posture is over-privileged).
- Tighten control-plane MI scope (currently RG-Contributor; should be
  per-resource grants only).
- Phase 5 bridge re-deploy so `rac-dev.rac.checkwithscience.com` actually
  routes (DNS Zone Contributor on the child zone).
- Flip `deployCustomDomain=true` on Front Door once a real cert is in
  place.
- Flip `deployTelemetryAlerts=true` once logs have flowed long enough for
  the referenced columns to exist.
