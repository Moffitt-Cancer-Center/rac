# RAC v1 Implementation Plan

Scope: Implements all 8 phases from `docs/design-plans/2026-04-23-rac-v1.md`.

## Phase order

1. `phase_01.md` — Repo scaffold + Tier 2 infrastructure foundation (Bicep, GHA deploy, alerts, Event Hub, Tier 3 resource group, managed identities)
2. `phase_02.md` — Control Plane skeleton (FastAPI, schema, submission CRUD, metric emitters)
3. `phase_03.md` — Build + Scan Pipeline (GHA, Syft, Grype, Defender, HMAC callbacks, webhook secret rotation)
4. `phase_04.md` — Pre-submission detection rule engine
5. `phase_05.md` — Approval workflow + Tier 3 provisioning (ACA, DNS, Key Vault) + cost attribution ingestion + per-app cost dashboard
6. `phase_06.md` — Token-Check Shim (Python) + shim metric emitters
7. `phase_07.md` — Reviewer token management + post-publication public mode
8. `phase_08.md` — Asset handling + manifest parsing

## Approved design deviations

The plan deliberately extends the design's enumerated data-plane table list with the following supporting tables. Each is necessary for an explicit design requirement or a cross-cutting pattern (append-only audit). Listed here so any later audit knows these were intentional and approved.

| Added table | Added in | Reason |
|---|---|---|
| `idempotency_key` | Phase 2 migration 0001 | Implements AC3.2. Postgres-backed store so idempotency works across multi-replica ACA. |
| `detection_finding_decision` | Phase 4 migration 0003 | Preserves strict append-only on `detection_finding` (AC12.1) while recording researcher decisions (AC4.3). |
| `app_ownership_flag` | Phase 5 migration 0004 | Implements the design's nightly Graph sweep output (AC9.2). |
| `app_ownership_flag_review` | Phase 5 migration 0004 | Append-only review decisions on flags, mirrors detection pattern. |

Two additional migrations do not add new tables but are called out for continuity:
- `0002_seed_web_ui_agent` — inserts the built-in `agent` row representing the Control Plane's own frontend (Phase 2 Task 11).
- `0005_rac_shim_db_role` — creates the least-privilege `rac_shim` Postgres role used by the Shim (Phase 6 Task 5; see role reconciliation note below).

## Cross-phase decisions pinned here

- **Shim language:** Python 3.12 (not Go). Rationale documented in `phase_06.md` preamble.
- **Postgres roles:** `rac_app` (Control Plane) and `rac_shim` (Shim) are separate roles. `rac_shim` has read on `reviewer_token`, read on `revoked_token`, read on `app`, read on `submission`, and INSERT on `access_log` (no UPDATE/DELETE). `rac_app` has read/write on the remaining tables subject to the append-only REVOKEs documented in Phase 2 Task 4. Created in migrations 0001 (rac_app) and 0005 (rac_shim).
- **Finalize trigger:** Asset-completion is signal-triggered (event emission from `assets/upload.finalize_upload` and `assets/external_fetch.fetch_external_asset`), not polled. See Phase 8 Task 7.
- **ORM model FCIS classification:** SQLAlchemy ORM models are classified as `# pattern: Imperative Shell` (not Functional Core). They carry session-coupled behavior (lazy loads, identity map) that breaks purity guarantees; tests must use `async_sessionmaker(expire_on_commit=False)` and explicitly avoid lazy loading. Applied consistently across Phases 2, 5, 7, 8.
- **Wake budget:** `settings.wake_budget_seconds` default 20s. Acceptance test in Phase 6 Task 10 asserts interstitial-to-upstream-200 wall clock ≤ budget.
- **Monorepo path equivalence:** Design references `apps/control-plane/src/...`. Plan implements `apps/control-plane/backend/src/rac_control_plane/...` because the control plane has both a Python backend and a React frontend. `apps/control-plane/backend/src/rac_control_plane/detection/rules/` IS the `apps/control-plane/src/detection/rules/` referenced by AC4.4 — AC verification treats the nested path as equivalent.
- **Phase 1 re-deploy loop:** Phase 1 infra-deploy is run twice — first time with `controlPlaneIdentityPrincipalId=''` (skipping the DNS role assignment), then after Phase 5 Task 1 creates the user-assigned MI, Phase 1 is re-run with the populated principal ID so the role assignment is created. Operational note captured in Phase 5 Task 1 and in `docs/runbooks/bootstrap.md`.

## Per-plan acceptance criteria traceability

Every AC from rac-v1.AC1 through rac-v1.AC12 maps to at least one phase's `Verifies:` field. The end-to-end verification tasks in each phase reference the AC identifiers literally; `test-requirements.md` (generated at the end of this planning session) is the authoritative cross-reference.

## File classification

Unless explicitly marked `// pattern: Functional Core` or `# pattern: Imperative Shell`, any file with runtime behavior is a bug. Type-only files, configuration files, IaC files, and test files are exempt (see each phase's File Classification Policy section).
