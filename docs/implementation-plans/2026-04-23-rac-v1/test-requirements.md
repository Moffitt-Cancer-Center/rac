# RAC v1 — Test Requirements

## Overview

This document maps every rac-v1 acceptance criterion (AC1 through AC12) to the phases and tasks that implement and test it. It is the authoritative cross-reference derived from the 8 phase implementation plans under `/home/sysop/rac/docs/implementation-plans/2026-04-23-rac-v1/`. For each sub-criterion it names the phase(s), the task(s) holding the test or verification, the kind of test, and the status of coverage (including composite ACs spread across multiple phases).

## Legend

- **Phase:** which phase implements it
- **Task(s):** which task(s) contain the test or verification
- **Test type:**
  - `unit` — pure-logic tests (Functional Core)
  - `integration` — tests that use testcontainers, fixtures, or SDK mocks spanning shell + core
  - `property` — Hypothesis-based property tests
  - `operational` — verified by executing commands against a real Azure subscription (no synthetic test asserts)
  - `acceptance` — the end-to-end/meta verification task at the end of a phase
  - `vitest` — frontend unit/component tests
- **Status:**
  - `automated` — code produces a repeatable test
  - `manual-operational` — verified by running on a real Azure subscription (Phase 1 / Phase 5 acceptance)
  - `partial` — split across phases; only complete when every contributing phase lands
  - `complete` — the final phase contributing to a composite criterion has landed all portions

## AC Traceability Table

| AC ID | Description | Phase(s) | Task(s) | Test type | Status | Notes |
|-------|-------------|----------|---------|-----------|--------|-------|
| rac-v1.AC1.1 | `infra-deploy.yml` against clean dev subscription provisions all Tier 2 resources without manual intervention | 1 | Tasks 3-13, 15, 17 | operational | manual-operational | Verified on real dev subscription via GHA workflow; `az bicep build` per module gives compile-level coverage |
| rac-v1.AC1.2 | `az deployment sub what-if` on unchanged environment produces zero changes | 1 | Tasks 14, 15, 17 | operational | manual-operational | Idempotency check after successful deploy |
| rac-v1.AC1.3 | Promotion workflow requires human approval before staging/prod | 1 | Tasks 15, 17 | operational | manual-operational | Enforced by GitHub Environment protection rules |
| rac-v1.AC1.4 | Missing required deployment parameter fails with actionable error naming the parameter | 1 | Tasks 13, 15, 17 | operational | manual-operational | Native ARM behavior; verified by temporarily removing `parentDomain` |
| rac-v1.AC1.5 | All provisioned resources carry required tags (`rac_env`) at creation | 1 | Tasks 2-13, 17 | operational | manual-operational | Verified via `az resource list` post-deploy |
| rac-v1.AC2.1 | Authenticated researcher submits valid GitHub URL + Dockerfile path; submission row created `awaiting_scan` with correct submitter + derived slug | 2 | Tasks 7 (slug derive), 10 (CRUD), 12 (UI), 14 (acceptance) | integration, vitest, property | automated | Property-based tests on slug derivation; integration tests against testcontainers Postgres |
| rac-v1.AC2.2 | Full FSM: `awaiting_scan → awaiting_research_review → awaiting_it_review → approved → deployed` with correct transitions and timestamps | 3, 5 | Phase 3 Task 8 (scan pass portion), Phase 5 Tasks 3, 4, 6, 10, 11 | integration, acceptance | complete | Scan-pass → `awaiting_research_review` in Phase 3; research/IT approvals + provisioning in Phase 5 |
| rac-v1.AC2.3 | Unauthenticated `POST /submissions` returns 401 | 2 | Tasks 5, 10 | integration | automated | `fastapi-azure-auth` enforces; asserted via integration test |
| rac-v1.AC2.4 | Submission with GitHub URL returning 404 surfaces validation error before pipeline dispatch | 2 | Task 10 (includes `services/github_validation.py`) | integration | automated | `respx` mocks GitHub; assert no row on 404 |
| rac-v1.AC2.5 | Submission with missing Dockerfile transitions to `pipeline_error`/`scan_rejected` with researcher-visible error | 3 | Task 8 (callback ingest handles `build_failed`) | integration, acceptance | automated | Phase 3 Task 10 also exercises via `invalid-dockerfile` golden repo |
| rac-v1.AC2.6 | Entra `oid` persisted as `submitter_principal_id` consistently across `submission`, `approval_event`, `detection_finding` | 2 | Tasks 5 (principal), 10 (CRUD) | integration | automated | Asserted directly in `test_submissions_api.py` |
| rac-v1.AC3.1 | OAuth2 client-credentials auth grants access; `agent_id` recorded on every submission created via that path | 2 | Tasks 6 (client creds), 10, 11 | integration | automated | |
| rac-v1.AC3.2 | Duplicate `POST /submissions` with same `Idempotency-Key` returns original `submission_id` at HTTP 200 (no new row) | 2 | Task 9 (middleware), 10 | integration | automated | Postgres-backed idempotency store (design deviation — new `idempotency_key` table) |
| rac-v1.AC3.3 | Registered webhook subscriber receives HMAC-signed callback for every matching state transition | 3 | Tasks 8 (ingest + deliver), 9B (secret rotation) | integration | automated | Includes 30-day HMAC secret rotation via ACA scheduled job (Task 9B) |
| rac-v1.AC3.4 | Pipeline callback with invalid HMAC signature returns 401; submission state unchanged | 3 | Tasks 3 (HMAC sign), 8 (ingest) | unit, property, integration | automated | `hmac_sign.py` property tests + Control Plane ingest 401 test |
| rac-v1.AC3.5 | Request from disabled agent returns 403 | 2 | Tasks 6, 10, 11 | integration | automated | |
| rac-v1.AC3.6 | Webhook subscription auto-disables after N consecutive delivery failures; visible in admin UI | 3 | Tasks 8 (deliver), 9 (admin UI) | integration, vitest | automated | |
| rac-v1.AC4.1 | Dockerfile `RUN wget https://...` fires `dockerfile/inline_downloads` rule; finding surfaced in UI with accept/override | 4 | Tasks 4 (rule), 8 (nudges UI), 9 (acceptance) | unit, property, vitest, acceptance | automated | |
| rac-v1.AC4.2 | Large file in git fires `repo/huge_files_in_git` rule | 4 | Tasks 5, 9 | unit, property | automated | |
| rac-v1.AC4.3 | Researcher decision on finding persisted with `rule_id`, `rule_version`, decision, timestamp | 4 | Tasks 6 (engine), 7 (findings API), 8 (UI) | integration, vitest | automated | New `detection_finding_decision` table (design deviation) preserves append-only semantics |
| rac-v1.AC4.4 | Adding new rule file to `detection/rules/` is picked up at service start without other edits | 4 | Task 2 (discovery), 9 (acceptance) | integration | automated | Path equivalence: design's `apps/control-plane/src/detection/rules/` = plan's `apps/control-plane/backend/src/rac_control_plane/detection/rules/` (README cross-phase decision) |
| rac-v1.AC4.5 | Service-to-service submission with detection hits lands in `needs_user_action` | 4 | Task 6 (orchestrator) | integration | automated | |
| rac-v1.AC4.6 | Two independent firings of the same rule produce two distinct `detection_finding` rows | 4 | Tasks 2, 4, 6 | integration | automated | |
| rac-v1.AC5.1 | Clean golden repo builds, pushes ACR, passes Grype+Defender, advances to `awaiting_research_review` | 3 | Tasks 2, 4, 5, 6 (`clean-python-flask` fixture), 8, 10 | unit, property, integration, acceptance | automated | |
| rac-v1.AC5.2 | Planted HIGH CVE blocked; submission `scan_rejected`; researcher sees CVE list in UI | 3 | Tasks 2, 4, 6 (`planted-cve-node`), 8, 9 (UI), 10 | unit, integration, vitest, acceptance | automated | |
| rac-v1.AC5.3 | Invalid Dockerfile transitions to `pipeline_error` with build log artifact accessible | 3 | Tasks 4, 6 (`invalid-dockerfile`), 8, 10 | integration, acceptance | automated | |
| rac-v1.AC5.4 | Defender scan timeout produces partial verdict; submission advances with "Defender scan pending" warning to IT approver | 3, 5 | Phase 3 Tasks 2, 3, 4, 8, 10 (verdict logic); Phase 5 Task 10 (UI badge assertion) | unit, property, integration, vitest, acceptance | complete | Explicit vitest assertion in Phase 5 Task 10 for `defender_timed_out=true` badge |
| rac-v1.AC5.5 | Layer cache is hit on second build of unchanged repo | 3 | Tasks 5, 6 (`large-repo`), 10 | acceptance | automated | Verified by wall-clock comparison across back-to-back `golden-repos-nightly` runs |
| rac-v1.AC6.1 | Final IT approval creates ACA app (correct image, env vars, scaling, tags); DNS A-record; per-app signing key; submission `deployed` | 5 | Tasks 5 (SDK wrappers), 6 (orchestrator), 11 | unit (tag_builder), integration, acceptance | automated | Tag assertion covered by Task 5 unit tests + Task 6 orchestrator tests |
| rac-v1.AC6.2 | App resolves at `https://<slug>.${PARENT_DOMAIN}`; cold start serves interstitial, wakes app, redirects within wake budget | 5, 6 | Phase 5 Task 11 (DNS resolves), Phase 6 Tasks 3 (cold-start decision), 6 (wake helper), 8 (main flow), 10 (acceptance) | unit, integration, acceptance | complete | Phase 5 verifies DNS + upstream responds internally; Phase 6 delivers interstitial + wake; `settings.wake_budget_seconds = 20` asserted in Phase 6 Task 10 |
| rac-v1.AC6.3 | Provisioning error surfaces in admin UI with retry control; submission stays `approved` until resolved | 5 | Tasks 6 (orchestrator), 7 (retry UI), 11 | integration, vitest, acceptance | automated | |
| rac-v1.AC6.4 | Re-submitted app updates `app.current_submission_id` atomically; previous ACR image retained | 5 | Tasks 6, 11 | integration, acceptance | automated | |
| rac-v1.AC7.1 | Valid token via URL sets HttpOnly/Secure/SameSite=Lax cookie; shim redirects to clean URL; `access_log` row written | 6, 7 | Phase 6 Tasks 2 (pure validation), 3 (cookie), 4 (KV cache), 8 (main flow), 10 (acceptance); Phase 7 Tasks 1 (JWS), 2 (issuer), 4 (API), 7 (UI), 8 (acceptance) | unit, property, integration, vitest, acceptance | complete | Phase 7 mints; Phase 6 validates |
| rac-v1.AC7.2 | Revoked token (`jti` in `revoked_token`) rejected within 60s; branded "revoked" page | 6, 7 | Phase 6 Tasks 4 (denylist cache 60s TTL), 8, 10; Phase 7 Tasks 3 (revoke service), 4 (API), 8 (acceptance) | integration, acceptance | complete | Cache TTL pinned at 60s to meet AC timing |
| rac-v1.AC7.3 | Expired token produces branded "expired" page with researcher contact + PI name | 6 | Tasks 2 (pure validation), 7 (templates), 8, 10 | unit, property, integration, acceptance | automated | |
| rac-v1.AC7.4 | Malformed/forged/wrong-audience token returns generic 403 page; no validation detail leaked | 6 | Tasks 2 (error taxonomy), 7 (template), 8, 10 | unit, property, integration, acceptance | automated | Phase 6 Task 10 explicitly asserts body does NOT contain "signature", "audience", "issuer", "traceback" |
| rac-v1.AC7.5 | App in `access_mode=public` serves all requests without token validation; `access_log` rows still written with `token_jti=NULL` | 6, 7 | Phase 6 Tasks 5 (access_record), 6 (proxy), 8, 10; Phase 7 Tasks 5 (toggle), 7 (UI), 8 (acceptance) | unit, integration, vitest, acceptance | complete | Phase 7 owns toggle endpoint; Phase 6 owns shim behavior |
| rac-v1.AC7.6 | Token issued for App A rejected at App B (audience-claim mismatch) | 6 | Tasks 2 (audience), 8, 10 | unit, property, integration, acceptance | automated | |
| rac-v1.AC8.1 | `upload` asset stored in Blob; sha256 computed server-side; mounted at declared `mount_path` | 8 | Tasks 3 (sha256 stream), 4 (upload flow), 6 (copy + mount), 7 (submission integration), 10 (acceptance) | unit, property, integration, acceptance | automated | |
| rac-v1.AC8.2 | `external_url` asset with matching sha256 fetched, verified, cached, mounted | 8 | Tasks 3, 5 (external fetch), 6, 7, 10 | integration, acceptance | automated | |
| rac-v1.AC8.3 | `external_url` asset with sha256 mismatch blocks deployment; IT approver sees both hashes | 8 | Tasks 5, 8 (missing_sha rule), 9 (hash-mismatch card UI), 10 | integration, vitest, acceptance | automated | |
| rac-v1.AC8.4 | `external_url` with unreachable URL puts submission in `needs_user_action` with clear explanation | 8 | Tasks 5, 7, 10 | integration, acceptance | automated | |
| rac-v1.AC8.5 | Submission without committed `rac.yaml` but form-declared assets produces equivalent parsed manifest | 8 | Tasks 1 (parser), 2 (form_mapper), 7 (create path), 9 (UI), 10 | unit, property, vitest, acceptance | automated | |
| rac-v1.AC8.6 | Manifest with `shared_reference` asset rejected at submit time with "coming soon" message naming entry | 8 | Tasks 1 (`reject_shared_references`), 7, 10 | unit, acceptance | automated | |
| rac-v1.AC9.1 | Submission captures `pi_principal_id` (validated against Entra/Graph) and `dept_fallback` | 5 | Tasks 2 (PI validation), 11 | unit, property, integration, acceptance | automated | |
| rac-v1.AC9.2 | Nightly Graph sweep detects deactivated PI and flags every owned app with "Owner deactivated" status | 5 | Tasks 8 (graph sweep + `app_ownership_flag` migration), 10 (UI), 11 | unit, property, integration, acceptance | automated | New tables `app_ownership_flag` and `app_ownership_flag_review` (design deviation) preserve append-only |
| rac-v1.AC9.3 | App's `pi_principal_id` can be transferred without losing audit history; `approval_event.actor_principal_id` unchanged | 5 | Tasks 9 (transfer service + API), 10 (UI), 11 | integration, vitest, acceptance | automated | |
| rac-v1.AC10.1 | Shim writes `access_log` row for every proxied request, including `access_mode=public` | 6 | Tasks 5 (access_record + batch writer), 8 (main flow), 10 | unit, integration, acceptance | automated | |
| rac-v1.AC10.2 | Custom metrics via OpenTelemetry | 2, 3, 5, 6 | Phase 2 Tasks 13B/13C (`rac.submissions.by_status` counter + `rac.approvals.time_to_decision_seconds` histogram declared); Phase 3 Task 8 (`scan_results/ingest.py` emits `rac.scans.verdict`); Phase 5 Task 4 (`services/approvals/record.py` wires approval histogram); Phase 6 Task 9B (`rac.shim.token_validations`, `rac.shim.wake_up_duration_ms`) | unit, integration | partial — all four portions required for complete coverage | **AC10.2 composite** — see per-phase notes; each phase wires its instrument + InMemoryMetricReader unit test |
| rac-v1.AC10.3 | Pager-tier alert for shim 5xx > 1% over 5 min fires and pages on-call (plus CP 5xx, Postgres conn fail, KV access denied, pipeline stuck) | 1 | Tasks 12C (alerts module), 16 (incident-response runbook), 17 (fault-injection smoke test) | operational | manual-operational | Fault injection documented in `docs/runbooks/incident-response.md`; acceptance verified post-deploy |
| rac-v1.AC10.4 | Shim logs are structured JSON with stable fields (`submission_id`, `app_id`, `principal_id`, `request_id`) in Log Analytics | 6 | Task 8 (structlog configured in main), 10 (acceptance confirms fields in Log Analytics) | integration, acceptance | automated | |
| rac-v1.AC10.5 | Event Hub export surface exists per `docs/runbooks/siem-export.md` and is subscribable without code changes | 1 | Tasks 12D (Event Hub + diagnostic settings module), 16 (`siem-export.md`), 17 (test consumer smoke test) | operational | manual-operational | Consumer subscribes via `Listen` authz rule; peek command in acceptance |
| rac-v1.AC11.1 | Every Azure resource carries `rac_env`, `rac_app_slug`, `rac_pi_principal_id`, `rac_submission_id` at creation | 1, 5 | Phase 1 Tasks 2 (tags helper), 3-13 (every module passes `tags`), 17 (acceptance query) [Tier 2]; Phase 5 Tasks 5 (`tag_builder`), 6 (orchestrator), 11 [Tier 3] | unit, property (tag_builder), integration, operational | complete | Phase 1 handles Tier 2 (`rac_env` required; Tier 2 resources are not app-scoped); Phase 5 handles Tier 3 (all four tags) |
| rac-v1.AC11.2 | Nightly Cost Management export ingests daily cost into `cost_snapshot_monthly`; admin dashboard surfaces per-app MTD spend | 5 | Task 10B (`services/cost/ingest.py`, `aggregation.py`, dashboard UI) | unit, property, integration, vitest | automated | Owner: Phase 5 Task 10B (added in finalization pass) |
| rac-v1.AC11.3 | Apps with `min-replicas=0` idle 30+ days appear in "scale-to-zero savings" dashboard row | 5 | Task 10B (`compute_idle_apps` in `aggregation.py`, dashboard UI) | unit, property, vitest | automated | |
| rac-v1.AC12.1 | Append-only tables (`approval_event`, `detection_finding`, `access_log`, `revoked_token`) have no UPDATE/DELETE grants to `rac_app` / `rac_shim` | 2, 4, 5, 6, 7 | Phase 2 Task 4 (migration 0001 REVOKEs + test); Phase 4 Task 6 (`detection_finding_decision` preserves append-only); Phase 5 Task 8 (`app_ownership_flag` + review migration 0004); Phase 6 Task 5 (access_log under `rac_shim` — migration 0005 creates role); Phase 7 Task 3 (revoke re-asserts) | integration | complete | Verified by inserting a row then asserting `UPDATE`/`DELETE` raises `InsufficientPrivilege` |
| rac-v1.AC12.2 | Every API error response body includes `correlation_id` present in App Insights trace | 2, 6 | Phase 2 Tasks 2 (correlation middleware), 3 (error handler); Phase 6 Task 10 (acceptance asserts `request_id` in body and Log Analytics line) | unit, integration, acceptance | complete | Control Plane + Shim both covered |
| rac-v1.AC12.3 | API error responses never leak Postgres error text, stack traces, internal URIs, or token validation detail | 2, 6 | Phase 2 Tasks 3 (error handler + `render_error` pure), Phase 6 Tasks 2 (error taxonomy), 7 (generic template), 10 (acceptance asserts forbidden strings) | unit, integration, acceptance | complete | |

## AC Groups Summary

- **AC1 (Platform deployable from source):** covered entirely by Phase 1 Tasks 2-17; fully addressed; all 5 sub-criteria verified operationally on real dev subscription.
- **AC2 (End-to-end submission):** covered by Phases 2 (create path: AC2.1, 2.3, 2.4, 2.6), 3 (scan-pass portion: AC2.2 partial, AC2.5), 5 (approval + provisioning: AC2.2 complete). All 6 sub-criteria fully addressed.
- **AC3 (API-first):** covered by Phase 2 (auth + idempotency: AC3.1, 3.2, 3.5) and Phase 3 (webhooks: AC3.3, 3.4, 3.6). All 6 sub-criteria fully addressed; AC3.3 includes webhook HMAC secret rotation (Phase 3 Task 9B).
- **AC4 (Pre-submission detection):** covered entirely by Phase 4; all 6 sub-criteria addressed with auto-discovery, pure rules, and append-only decisions.
- **AC5 (Build + scan pipeline + severity gate):** covered by Phase 3 (all cases), with Phase 5 Task 10 adding the explicit Defender badge vitest assertion for AC5.4. All 5 sub-criteria fully addressed.
- **AC6 (Approved apps deploy):** covered by Phase 5 (provisioning: AC6.1, 6.3, 6.4; AC6.2 DNS/upstream) and Phase 6 (AC6.2 cold-start interstitial + wake). All 4 sub-criteria fully addressed.
- **AC7 (Reviewer tokens):** covered by Phase 6 (shim validation: AC7.1-7.6 runtime enforcement) and Phase 7 (issuance + revocation + access_mode: AC7.1, 7.2, 7.5 control-plane side). All 6 sub-criteria fully addressed.
- **AC8 (Asset handling):** covered entirely by Phase 8; all 6 sub-criteria addressed (upload, external_url, hash mismatch, unreachable URL, form fallback, shared_reference rejection).
- **AC9 (Ownership):** covered entirely by Phase 5; all 3 sub-criteria addressed (PI validation, nightly sweep, transfer-preserves-audit).
- **AC10 (Observability):** spans Phase 1 (alerts AC10.3; SIEM Event Hub AC10.5), Phase 2 (metrics AC10.2 partial — submissions + approvals histogram declaration), Phase 3 (AC10.2 partial — scan verdict), Phase 5 (AC10.2 partial — approvals histogram emission), Phase 6 (AC10.1 access_log per request; AC10.2 partial — shim metrics; AC10.4 structured logs). All 5 sub-criteria fully addressed when every AC10.2 portion lands.
- **AC11 (Cost attribution):** covered by Phase 1 (Tier 2 portion of AC11.1), Phase 5 (Tier 3 portion of AC11.1 + AC11.2 + AC11.3 via Task 10B). All 3 sub-criteria fully addressed.
- **AC12 (Audit + error hygiene):** covered by Phase 2 (AC12.1 migration, AC12.2 correlation ID, AC12.3 safe errors), Phase 4/5/6/7 (AC12.1 append-only preserved via new decision/review/shim-role migrations), Phase 6 (AC12.2/12.3 shim error hygiene). All 3 sub-criteria fully addressed across Control Plane + Shim.

## Manual Verification Checklist

The following ACs are verified operationally against a real Azure subscription during the Phase 1 bootstrap and the Phase 5 end-to-end smoke test. Commands below are lifted verbatim from the phase verification tasks.

### Phase 1 — Tier 1 bootstrap and platform deploy (operational ACs)

Run after Tier 1 prerequisites (bootstrap runbook `/home/sysop/rac/docs/runbooks/bootstrap.md`) are satisfied and the first `infra-deploy.yml` run against the dev subscription has completed.

**rac-v1.AC1.1 — clean-deploy success:**
```bash
# Trigger GHA workflow via workflow_dispatch with environment=dev
# Confirm deploy-dev completes without manual intervention beyond GH Environment approval
# Record deployment name + resource group name
```

**rac-v1.AC1.2 — idempotent what-if:**
```bash
az deployment sub what-if \
  --location ${AZURE_LOCATION} \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam
# Output must show "no changes" (or only provisioningState metadata drift)
```

**rac-v1.AC1.3 — human approval gate:**
```
# Observe that deploy-staging and deploy-prod jobs are blocked pending reviewer
# approval in GitHub Actions. Approval must come from someone other than the
# workflow trigger author.
```

**rac-v1.AC1.4 — missing parameter error clarity:**
```bash
# Temporarily remove parentDomain from dev.bicepparam in a scratch branch
az deployment sub what-if \
  --location ${AZURE_LOCATION} \
  --template-file infra/main.bicep \
  --parameters infra/environments/dev.bicepparam
# Confirm Azure returns an error naming parentDomain as missing
# Revert the scratch change
```

**rac-v1.AC1.5 — tag-at-creation:**
```bash
az resource list \
  --resource-group rg-rac-dev \
  --query "[?tags.rac_env!='dev'].{name:name, type:type}" \
  -o table
# Output must be empty — every resource has rac_env=dev at creation
```

**rac-v1.AC11.1 (Tier 2 portion) — tag completeness:**
```bash
az resource list \
  --resource-group rg-rac-dev \
  --query "[?tags.rac_env==null || tags.rac_managed_by==null]" \
  -o table
# Output must be empty
```

**rac-v1.AC10.3 — alert fault injection (Phase 1 Task 12C / Task 17):**
```
# Trigger the controlled fault injection per docs/runbooks/incident-response.md:
# post 100 requests returning 503 to the dev shim endpoint (after Phase 6 deploys),
# wait 5 min, confirm the action group's email/webhook activated.
# Azure Portal: Monitor → Alerts → Alert history, or action group test notification history.
# Note: Shim/CP metric alerts deferred until Phase 2/6 deploy; Postgres + Key Vault
# alerts are verifiable at Phase 1 stage alone.
```

**rac-v1.AC10.5 — Event Hub SIEM peek (Phase 1 Task 12D / Task 17):**
```bash
# After Phase 2 deploy to dev, trigger one submission event, wait up to 5 min for
# Log Analytics ingestion:
az eventhubs eventhub message receive \
  --namespace-name evhns-rac-dev \
  --eventhub-name eh-rac-access-logs \
  --resource-group rg-rac-dev \
  --count 5
# Confirm at least one event arrives. Record JSON in acceptance report.
```

Write findings to `${SCRATCHPAD_DIR}/phase1-acceptance-report.md`.

### Phase 5 — Full submission lifecycle acceptance (operational ACs)

Run after Phase 5 has deployed to dev with real ACA env, ACR, Key Vault, DNS zone, and Azure Files storage account.

**rac-v1.AC2.2 / AC6.1 / AC11.1 (Tier 3) — happy-path submission lifecycle:**
```bash
# 1. Submit clean-python-flask golden repo → pipeline passes (Phase 3) → submission
#    reaches awaiting_research_review.
# 2. Research approver approves → awaiting_it_review; approval_event has correct
#    actor OID (AC2.2).
# 3. IT approver approves → provisioning runs.
az containerapp show --name <slug> --resource-group rg-rac-tier3-dev
# Assert env_vars, image, min_replicas=0, HTTP scaler, and tags
# (rac_app_slug, rac_pi_principal_id, rac_submission_id, rac_env) all present.

az network dns record-set a show --zone-name ${PARENT_DOMAIN} --name <slug> --resource-group rg-rac-dev
# Confirm A record points at the App Gateway IP.

az keyvault key show --vault-name <kv-name> --name rac-app-<slug>-v1
# Confirm signing key exists (AC6.1).
```

**rac-v1.AC6.2 (pre-Shim, internal-resolution portion):**
```bash
# Phase 5 alone: Shim (Phase 6) not yet deployed.
# az containerapp exec into the Control Plane and curl internal hostname:
az containerapp exec --name <control-plane-app> --resource-group rg-rac-dev -- \
  curl -v http://<slug>.internal.<env>.azurecontainerapps.io/
# Expect 200 from researcher app.
```

**rac-v1.AC6.4 — re-submission atomic update:**
```bash
# Submit the same slug again with a new commit.
# Verify app.current_submission_id updates atomically; old image
# (<slug>:<old_submission_id>) still exists in ACR.
```

**rac-v1.AC9.2 — Graph sweep:**
```bash
# Disable a PI in Entra (test-only)
python -m rac_control_plane.cli.graph_sweep
# Verify app_ownership_flag row appears.
```

**rac-v1.AC9.3 — ownership transfer preserves audit:**
```sql
-- After admin transfers ownership to a new PI:
SELECT actor_principal_id FROM approval_event
WHERE app_id='<app>' AND kind='research_decision';
-- Must return the ORIGINAL approver's OID, unchanged.
```

**rac-v1.AC6.3 — provisioning failure retry:**
```
# Simulate DNS quota exhaustion.
# Verify provisioning fails; submission stays approved; admin UI surfaces retry.
# Retry succeeds after quota restored.
```

Write findings to `${SCRATCHPAD_DIR}/phase5-acceptance-report.md`.

### Phase 6 — Reviewer access end-to-end (Shim operational verification)

Run after Phase 6 deployment on dev; complements AC7 verification in Phase 7 acceptance.

**rac-v1.AC7.1 — cookie flags and redirect:**
```bash
# Mint valid JWT via test fixture (Key Vault key from Phase 5) with aud=rac-app:<slug>, 1h expiry
curl -v "https://<slug>.${PARENT_DOMAIN}/?rac_token=<jwt>"
# Expect 302 to https://<slug>.${PARENT_DOMAIN}/ with Set-Cookie: rac_session=... HttpOnly; Secure; SameSite=Lax
```

Verify access_log row:
```sql
SELECT * FROM access_log WHERE reviewer_token_jti = '<jti>';
-- At least one row present (AC7.1, AC10.1).
```

**rac-v1.AC7.6 — wrong-audience rejection:**
```bash
# Mint token for App A, request App B → expect 403 error_generic page.
```

**rac-v1.AC7.3 — expired token:**
```bash
# Mint an expired token → expect 403 error_expired page with PI name + contact email.
```

**rac-v1.AC7.2 — revocation within 60s:**
```sql
-- After minting a valid token:
INSERT INTO revoked_token (jti, ...) VALUES ('<jti>', ...);
```
```bash
sleep 60
curl "https://<slug>.${PARENT_DOMAIN}/?rac_token=<jwt>"
# Expect 403 error_revoked.
```

**rac-v1.AC7.4 — no detail leak:**
```bash
# Mint forged token (wrong key):
curl "https://<slug>.${PARENT_DOMAIN}/?rac_token=<forged_jwt>"
# Assert response body does NOT contain "signature", "audience", "issuer",
# "traceback", "Traceback", or any internal hostname.
```

**rac-v1.AC7.5 — public access mode:**
```sql
UPDATE app SET access_mode='public' WHERE id='<app_id>';
```
```bash
curl "https://<slug>.${PARENT_DOMAIN}/"
# Expect 200; access_log.reviewer_token_jti IS NULL, access_mode='public'.
```

**rac-v1.AC6.2 — cold-start wake budget:**
```bash
# Scale researcher app to zero explicitly
# Record wall clock T0
curl "https://<slug>.${PARENT_DOMAIN}/"
# Interstitial HTML returned
# Record wall clock T1 when upstream 200 received
# Assert (T1 - T0) <= settings.wake_budget_seconds (default 20s).
```

**rac-v1.AC12.2 — correlation-id round-trip:**
```bash
# Present a revoked token:
curl -v "https://<slug>.${PARENT_DOMAIN}/?rac_token=<revoked_jwt>" 2>&1 | grep request_id
# Capture request_id from body. Query Log Analytics:
# Confirm the same request_id appears in the structured log entry.
```

**rac-v1.AC12.3 — no internal-detail leak:**
```bash
# Already covered under AC7.4 — verify error_generic page body does NOT include
# "signature", "audience", "issuer", "traceback", or internal hostnames.
```

Write findings to `${SCRATCHPAD_DIR}/phase6-acceptance-report.md`.

### Traceability

Every AC in rac-v1 (AC1 through AC12) is either:
- Verified by at least one automated test (unit / property / integration / vitest / acceptance), OR
- Verified by at least one manual operational command above (AC1.*, AC10.3, AC10.5, Phase 5 lifecycle, Phase 6 runtime).

Composite ACs (AC10.2, AC11.1, and any append-only variant of AC12.1) are marked complete only when every contributing phase has delivered its portion — see the Traceability Table notes.
