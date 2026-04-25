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
