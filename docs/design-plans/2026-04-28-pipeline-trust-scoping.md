# Pipeline Trust Setup — Design Plan Scoping

**Status:** Scoping memo. Use as input to a `start-design-plan` skill invocation.
**Author:** captured 2026-04-28 evening from a deploy-investigation session.
**Linked memory:** `project_rac_pipeline_trust_scoping.md` (pointer).

## Why this needs a design plan, not just a fix

The build-and-scan pipeline (`jdkruzr/rac-pipeline`, separate repo) cannot
authenticate to Azure today. Submission #4 (2026-04-28 17:55 UTC) was the first
to dispatch successfully, and the workflow died at the **second step** —
`Azure login (OIDC)` — because `secrets.AZURE_CLIENT_ID` / `AZURE_TENANT_ID` /
`AZURE_SUBSCRIPTION_ID` are unset on the repo, and even if they were set there
is **nothing on the Azure side to authenticate against**: no Entra app, no
user-assigned MI, no federated identity credential, no RBAC.

This blocks every submission's progression past `awaiting_scan`. It is the
gating item for further end-to-end work.

The shape is non-trivial because:

- The pipeline is invoked from GitHub Actions OIDC, not from anything inside
  the tenant. The trust must be established cross-cloud.
- Each RAC tenant is its own Azure subscription. The `rac-pipeline` repo is
  shared across tenants. So the design must cover N tenants, not 1.
- Several decisions in scope (UAMI vs Entra app, RBAC granularity, callback
  secret pattern) interact with each other.

## What exists today (verified 2026-04-28)

- `jdkruzr/rac-pipeline` workflow file `.github/workflows/build-and-scan.yml`
  uses `azure/login@v2` with `client-id` / `tenant-id` / `subscription-id`
  from secrets. Permissions: `id-token: write`, `contents: read`. Workflow
  triggers: `repository_dispatch` (event type `rac_submission`) and
  `workflow_call`.
- `gh secret list --repo jdkruzr/rac-pipeline` → empty.
- `gh variable list --repo jdkruzr/rac-pipeline` → empty.
- `gh api /repos/jdkruzr/rac-pipeline/environments` → `total_count: 0`.
- `az ad app list --display-name 'rac-pipeline'` → empty.
- `az identity list -g rg-rac-dev` → `id-rac-shim-dev`, `id-rac-appgw-dev`,
  `id-rac-controlplane-dev` only. No pipeline UAMI.
- `infra/main.bicep` and modules → only the *control-plane*-side pipeline wiring
  (`controlPlaneGithubPipelineOwner`, `controlPlaneGithubPipelineRepo`,
  `RAC_GH_PAT` secret reference). No federated-credential resources, no
  pipeline-side identity at all.
- The `Microsoft.Insights/diagnosticSettings` block I added today (commit
  `d569ead`) for AppGw → Log Analytics is *unrelated* to this story but is
  the reason we now have a place to query workflow-side correlation when this
  lands.

## What needs to exist (target state, in plain English)

A new pipeline identity per tenant, trusted by the shared `jdkruzr/rac-pipeline`
GitHub repo, with the minimum RBAC to run a build-and-scan. Specifically:

1. A user-assigned managed identity (or Entra app — *decision pending*) per
   tenant deploy, e.g. `id-rac-pipeline-dev`.
2. Federated identity credential(s) on that identity trusting
   `token.actions.githubusercontent.com` with subject scoped to
   `repo:jdkruzr/rac-pipeline:...` for the events that matter.
3. RBAC on the tenant's Azure resources: AcrPush on the ACR, AcrPull on the
   ACR (separate role), Storage Blob Data Contributor on the artifacts
   container, Key Vault Secrets User on the KV (so the pipeline can fetch
   the per-submission callback secret), maybe Defender-related role for scan
   polling.
4. Repo secrets (3): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`. Repo variables (5): `ACR_NAME`,
   `ACR_LOGIN_SERVER`, `BLOB_ACCOUNT_URL`, `KV_NAME`, `SEVERITY_GATE`.
5. Bicep codification of items 1, 2, 3 so a fresh tenant deploy provisions
   the trust automatically.
6. A documented setup step (in the bootstrap runbook) for items 4, since
   GitHub-side configuration cannot be done from Azure bicep.

## Decisions the design plan must resolve

These are the core "what shape should this take" questions. Each one is
non-obvious; default answers are listed but the brainstorm should challenge
them.

1. **Identity type — UAMI vs Entra app registration?**
   Default: UAMI. Reasoning: UAMIs are bicep-native, scoped per resource
   group, and don't require Application Administrator privileges to create.
   Entra apps are more flexible but require directory-level rights to provision
   and they live outside the bicep module's natural scope (subscription).
   Counter-argument for Entra app: federated credentials on Entra apps can
   trust audiences beyond `api://AzureADTokenExchange`, useful if a future
   integration needs that.
   The chosen answer determines almost everything below it.

2. **Federated credential subject scope.**
   Default: one credential trusting `repo:jdkruzr/rac-pipeline:ref:refs/heads/main`
   plus a second trusting `repo:jdkruzr/rac-pipeline:ref:refs/heads/*` if we
   want feature branches to also dispatch in dev (probably not). For
   `repository_dispatch` events the subject is still
   `repo:<owner>/<repo>:ref:<branch>`. **Verify this by checking GitHub OIDC
   token claims documentation** — this is exactly the kind of detail the
   design plan should pin down with a citation, not assumption.

3. **RBAC granularity — broad role assignments or fine-grained?**
   Default: minimum necessary roles per resource, attached at the resource
   scope (not RG or subscription). Specifically: AcrPush + AcrPull on ACR
   only, KV Secrets User on KV only, Storage Blob Data Contributor on the
   artifacts container only. **Question:** does the pipeline need to read
   anything from the control plane's KV, or just the per-submission callback
   secret? If only the latter, can the role be scoped to a *specific secret
   prefix*? Azure RBAC on KV is scope-based, not name-based, so this is
   probably not directly possible — but it's worth checking before the design
   plan settles.

4. **Callback secret pattern — per-submission vs static signing key?**
   Today's pattern: each submission has its own KV secret named
   `rac-pipeline-cb-<submission_id>`, minted by the control plane (or
   *supposed* to be — see "Adjacent issues" below) just before dispatch and
   fetched by the pipeline at callback time. Alternative: a single static
   HMAC signing key (similar to the reviewer-token JWS pattern) used for all
   pipeline-→-CP callbacks, with the submission_id in the signed body acting
   as the binding. **Trade-off:** per-submission keys give per-submission
   blast-radius isolation if a key leaks; static key is simpler to provision,
   has lower KV churn, and aligns with the JWS pattern we already use for
   reviewer tokens. The design plan should pick one and justify.

5. **Multi-tenant story — what's shared, what's per-tenant?**
   The `rac-pipeline` repo is shared across tenants. That means a single
   workflow run dispatches with a payload that names which tenant's CP it's
   building for. Each tenant's federated credential independently trusts the
   *same* GitHub repo subject — that's fine, federated credentials don't
   collide. But this implies: the GH repo secrets/variables (`AZURE_CLIENT_ID`,
   `ACR_NAME`, etc.) are **per-tenant** but configured at the **shared repo**.
   That doesn't work as repo secrets — needs **GitHub Environments**, one per
   tenant, with the workflow specifying which environment via `environment:`
   keyed off a workflow input. **Alternative:** the workflow takes everything
   from `inputs` (passed in the dispatch payload), so no repo-level secrets
   exist; the CP signs and includes everything in the payload. Currently
   secrets are *not* in the payload and that's deliberate (they shouldn't be).
   So GH Environments + dynamic environment selection per dispatch is likely
   the right answer. The design plan must confirm this works with
   `repository_dispatch` events (it does for `workflow_dispatch`; less clear
   for `repository_dispatch`).

6. **Secret rotation.**
   For the GH repo secrets (`AZURE_CLIENT_ID` etc.): these don't actually
   need rotation in the OIDC model — they're identifiers, not credentials.
   For the federated credential subject: doesn't need rotation. For the
   per-submission KV callback secrets: TTL'd already (2× pipeline timeout
   per `mint_callback_secret`). So rotation story is mostly free. Still, the
   design plan should make this explicit.

7. **What about the existing `_build_dispatch_fn` "silently skip if no PAT"
   pattern?** The control plane currently uses a personal access token to
   call GitHub's `repository_dispatch` API. If that PAT is missing, it logs
   a warning and silently no-ops the dispatch (which is how submissions #2
   and #3 ended up stranded). With the retry endpoint in place
   (commit `0192e0c`), surfacing 503 on retry is loud. But the *original*
   create-submission path is still silent. Two options the design plan can
   address: (a) make the original path also surface 503 / fail submission
   creation if dispatch is unconfigured, treating "no dispatch path" as a
   config error rather than a soft-skip; (b) replace the PAT with a GitHub
   App, which (with installation tokens) is more rotation-friendly and
   doesn't tie the dispatch identity to a single human's account. The
   GitHub App path is design-plan-sized on its own and may be a separate
   plan, but it's adjacent enough to mention here.

## Adjacent issues to fold in (or note as out-of-scope)

These are real bugs found during today's investigation. The design plan
should explicitly say which it intends to address.

- **`create_submission` placeholder secret bug.** `services/submissions/create.py:200`
  builds the payload with a placeholder secret name and never calls
  `mint_callback_secret`. Even if the pipeline auth worked today, it would
  fail at "Fetch callback secret" because the secret doesn't exist. The
  retry helper (commit `0192e0c`) does mint properly. The design plan
  should either fix the original path as part of this work or explicitly
  defer (with rationale).
- **Idempotency middleware 5xx semantics inconsistency.** Already in
  followups #4. Touches the dispatch flow because a 5xx from `create_submission`
  is exactly the path where this kicks in. Probably out of scope for this
  design plan but mention.
- **Graph 403 surfacing as 500.** Already in tactical-fixes list. Out of
  scope for this design plan; just note that the same "loud failure"
  principle applies to several places.

## Constraints to honor

- **Functional Core / Imperative Shell.** Anything new in the bicep is
  Imperative Shell by definition (it provisions). Anything new in Python
  needs the right pattern marker and FCIS discipline. The retry helper
  added today is `# pattern: Imperative Shell` correctly.
- **Forward-only migrations.** Any new DB tables (likely none for this
  plan) follow the strict forward-only rule.
- **Tenant portability.** Anything tenant-specific must be a bicep
  parameter, not a hard-coded value. The `rac-pipeline` GitHub owner/repo
  pair is already a bicep parameter (`controlPlaneGithubPipelineOwner` /
  `Repo`).
- **Secrets posture.** No long-lived SP secrets. Federated identity
  credentials only on the pipeline side. PAT-vs-GitHub-App for the
  control-plane → GitHub dispatch is an open question (decision #7).
- **Test discipline.** New helpers TDD'd. Bicep validated with
  `az deployment sub validate` + `what-if` per `infra/CLAUDE.md`.

## Files that will probably change

Drafted ahead so the design plan can compute scope accurately. None of
these are "definitely will change" — they're the candidate set.

- `infra/main.bicep` — wire up new pipeline identity module.
- `infra/modules/pipeline-identity.bicep` — **new** — UAMI + federated
  cred + RBAC.
- `infra/modules/role-assignments.bicep` — possibly extended for the
  pipeline RBAC, or those go in the new module above.
- `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py`
  — fix the placeholder-secret bug, possibly switch to "loud failure" on
  no-PAT.
- `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/retry.py`
  — possibly add the KV-lookup-then-mint logic for secret reuse.
- `docs/runbooks/bootstrap.md` — add a "configure GitHub repo
  secrets/environments" section.
- `apps/control-plane/backend/CLAUDE.md` — note the pipeline identity
  contract.

## Out of scope for this plan

- Logging aggregation (followup #9). Separate design plan when ready.
- WAF carve-out. Separate, smaller, already in followups.
- UI rename, timestamp display, etc. — all already in followups.
- Switching control-plane → GitHub dispatch from PAT to GitHub App.
  Mentioned in decision #7 but flagged as a separate plan if pursued.

## Inputs to load when starting the design plan

When the next session opens to begin the design plan, load these in order:

1. This document (`docs/design-plans/2026-04-28-pipeline-trust-scoping.md`).
2. The followups memory (`project_rac_pending_followups.md`) for adjacent
   context.
3. The Graph-500 diagnosis memory (`project_rac_graph_500_diagnosis.md`)
   only if the design touches graceful-failure mapping.
4. `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/`
   — all four files (`payload.py`, `secret_mint.py`, `github.py`, `retry.py`).
5. `/home/sysop/rac-pipeline/.github/workflows/build-and-scan.yml`.
6. `infra/main.bicep` (search for `pipeline`).
7. `docs/runbooks/bootstrap.md` (sections 3 and the "Federated Identity
   Credentials for GHA" section).

That should be enough to write the design plan from a cold start in 1–2
focused sessions.
