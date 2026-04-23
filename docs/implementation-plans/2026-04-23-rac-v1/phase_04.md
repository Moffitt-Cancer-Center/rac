# Phase 4: Pre-submission detection rule engine

**Goal:** A pluggable rule engine that runs detection rules (Dockerfile hygiene, repo hygiene, manifest hygiene) against a just-submitted submission, produces structured `Finding` records, persists them with rule version, and surfaces them in the UI as interactive nudges. A researcher's decision (accept / override / auto-fix / dismiss) is persisted with `rule_id@version`. Adding a new rule file to `detection/rules/` is picked up on service start without modifying registration code. Service-to-service submissions with detection hits land in `needs_user_action` rather than blocking on interactive input.

**Architecture:** FCIS throughout. Each rule is a pure function `def evaluate(ctx: RepoContext) -> Iterable[Finding]`. The `RepoContext` is a pre-built, immutable snapshot of everything a rule might need (Dockerfile contents as string + AST, list of repo file paths with sizes, manifest parsed dict, submission metadata). A single Imperative Shell builder materializes `RepoContext` once per submission from git clone + manifest parse + file stat; then all rules run over it in-process, deterministically. Rule discovery is auto-scan of `detection/rules/` via `pkgutil.walk_packages` at service startup; each rule module declares a module-level `RULE: Rule` constant. No database entries — rules ship as code per the design.

**Tech Stack:** Python `ast`-style Dockerfile tokenizer (we use `dockerfile-parse` package from Red Hat; fallback to a tiny hand-rolled tokenizer if dependency review flags it). `git` (subprocess via `dulwich` if we want pure-Python, or subprocess for simplicity) for cloning researcher repos at submission time. Hypothesis for property tests.

**Scope:** Phase 4 of 8.

**Codebase verified:** 2026-04-23 — Phases 2 & 3 delivered. `apps/control-plane/backend/src/rac_control_plane/services/` exists; `detection/` does not yet. No external dep on `dockerfile-parse` is installed; Phase 4 adds it.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC4: Pre-submission detection rules operate
- **rac-v1.AC4.1 Success:** A submission with `RUN wget https://...` in its Dockerfile fires the `dockerfile/inline_downloads` rule and surfaces the finding in the researcher UI with an accept/override choice.
- **rac-v1.AC4.2 Success:** A submission with a file larger than the configured threshold committed to git fires the `repo/huge_files_in_git` rule.
- **rac-v1.AC4.3 Success:** A researcher's decision (accept, override, auto-fix, dismiss) on a finding is persisted with `rule_id`, `rule_version`, decision, and timestamp.
- **rac-v1.AC4.4 Success:** Adding a new rule file to `apps/control-plane/src/detection/rules/` is picked up on service start without modifying registration or orchestration code elsewhere. **Path equivalence note:** The design says `apps/control-plane/src/detection/rules/`; this plan implements `apps/control-plane/backend/src/rac_control_plane/detection/rules/` because the control plane has a `backend/` subfolder (see README.md cross-phase decisions). These are the same location; any AC verification tool should treat them as equivalent.
- **rac-v1.AC4.5 Success:** A service-to-service submission (via client credentials) with detection hits lands in `needs_user_action` state rather than attempting to render interactive nudges.
- **rac-v1.AC4.6 Edge:** Two independent firings of the same rule on one submission produce two distinct `detection_finding` rows.

**Verifies:** Functionality phase. Each task names which AC cases it tests.

---

## File Classification Policy

- `detection/contracts.py`: type-only dataclasses/TypedDicts exempt; any helpers → Functional Core.
- Every rule module in `detection/rules/**`: Functional Core (pure evaluator).
- `detection/engine.py`: mixed — rule discovery is Imperative Shell (filesystem walk); rule invocation is pure given `RepoContext`. Split into `discovery.py` (Shell) and `evaluate.py` (Functional Core).
- `detection/repo_context.py`: Imperative Shell (git clone, file I/O).
- `detection_finding_store.py`: Imperative Shell.
- API routes, UI components: per Phase 2 convention.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Detection contracts — Rule, Finding, RepoContext

**Verifies:** Foundation for AC4.1 – AC4.6

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/contracts.py` (type-only; no FCIS tag)
- Create: `apps/control-plane/backend/tests/test_detection_contracts.py`

**Implementation:**

`contracts.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping

Severity = Literal["info", "warn", "error"]

@dataclass(frozen=True)
class RepoFile:
    path: str            # relative from repo root
    size_bytes: int
    # We intentionally do NOT include the full content. Rules that need content read it explicitly via RepoContext.read().

@dataclass(frozen=True)
class RepoContext:
    repo_root: Path
    submission_id: UUID
    dockerfile_path: str
    dockerfile_text: str
    files: tuple[RepoFile, ...]        # all non-gitignored tracked + untracked files
    manifest: Mapping | None           # parsed rac.yaml as dict, or None if absent
    submission_metadata: Mapping       # { "pi_principal_id": ..., "paper_title": ..., "agent_kind": ... }
    def read(self, path: str) -> bytes: ...   # reads a file from the cloned repo; raises if absent

@dataclass(frozen=True)
class Finding:
    rule_id: str                       # e.g. "dockerfile/inline_downloads"
    rule_version: int                  # incremented by author when rule logic changes
    severity: Severity                 # "warn" by default; only graduates to "error" with evidence
    title: str                         # short, user-facing
    detail: str                        # markdown-safe explanation
    line_ranges: tuple[tuple[int, int], ...] = ()    # Dockerfile line refs (1-based) when applicable
    file_path: str | None = None                     # file path the finding concerns
    suggested_action: Literal["accept", "override", "auto_fix", "dismiss"] | None = None
    auto_fix: AutoFixAction | None = None            # if a safe programmatic fix exists

@dataclass(frozen=True)
class AutoFixAction:
    kind: Literal["replace_line", "add_line", "remove_line", "apply_patch"]
    file_path: str
    payload: str                       # new content / patch

@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: int
    default_severity: Severity
    evaluate: Callable[[RepoContext], Iterable[Finding]]
```

Tests: construct instances, assert immutability (raising on field reassignment), assert equality / hashability of frozen dataclasses, assert `Finding.rule_id` is a string without whitespace.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_detection_contracts.py -v
```

**Commit:** `feat(detection): contracts (Rule, Finding, RepoContext)`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Rule discovery and evaluator

**Verifies:** `rac-v1.AC4.4`, `rac-v1.AC4.6`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/discovery.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/evaluate.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_rule_discovery.py`
- Create: `apps/control-plane/backend/tests/test_rule_evaluation.py`

**Implementation:**

`discovery.py`: `def load_rules(package: str = "rac_control_plane.detection.rules") -> dict[str, Rule]`. Uses `pkgutil.walk_packages` to walk the package, `importlib.import_module` for each submodule, and reads `RULE` module attribute. Builds a `{rule.rule_id: rule}` dict. Caches at service startup (stored on `app.state.rules`).

Rule modules convention: `detection/rules/<category>/<name>.py` (e.g., `rules/dockerfile/inline_downloads.py`). Each module MUST declare a module-level `RULE: Rule`. Missing `RULE` → `discovery.py` logs warning and skips the module. Duplicate `rule_id` across modules → startup error.

`evaluate.py` (pure): `def run_all(rules: Iterable[Rule], ctx: RepoContext) -> list[Finding]`. Calls each rule's `evaluate(ctx)`, collects findings, and preserves duplicates: if the same rule emits two findings on different Dockerfile lines, both are retained as separate `Finding` instances (AC4.6).

`tests/test_rule_discovery.py`:
- Drop two fixture rule files into a tmp package, call `load_rules`, assert both are discovered without any registration list being modified.
- Drop a rule file missing `RULE` → warning, but other rules still load.
- Drop two rules with the same `rule_id` → `DuplicateRuleIdError` raised.
- Regression test for AC4.4: assert the function signature and behavior does not require editing any other file to add a rule.

`tests/test_rule_evaluation.py`:
- Given two rules each emitting one finding on the same `RepoContext`, `run_all` returns two findings.
- Given one rule emitting two findings (AC4.6) on different lines, `run_all` returns both.
- Rule that raises → failure is contained; `run_all` logs the error and continues with other rules, returns a synthetic `Finding` of `severity="warn"`, `rule_id=<rule>`, `title="rule error"`, so operators see the breakage.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_rule_discovery.py apps/control-plane/backend/tests/test_rule_evaluation.py -v
```

**Commit:** `feat(detection): rule discovery + evaluator`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: RepoContext builder (shell)

**Verifies:** Foundational for AC4.1 – AC4.2

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/repo_context.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_repo_context.py`

**Implementation:**

`repo_context.py`:
- `async def build_repo_context(submission: Submission, workdir: Path) -> RepoContext`:
  1. `git clone --depth 1 --branch <ref> <repo_url> <workdir>/repo` (subprocess; surface `GitError` on failure — shell catches and converts).
  2. Walk filesystem under `<workdir>/repo` excluding `.git/`. For each file, record path + size; produce `tuple[RepoFile, ...]`.
  3. Read `dockerfile_path` from disk → `dockerfile_text`.
  4. If `rac.yaml` exists at repo root, load + parse (`yaml.safe_load`) → `manifest`; else `None`. (Actual schema validation deferred to Phase 8; this is raw dict.)
  5. Build `submission_metadata` from the ORM row (agent kind, pi_principal_id, paper_title).
  6. Return `RepoContext` with `repo_root=<workdir>/repo`.
- `RepoContext.read(self, path)` is implemented on a thin helper class co-located here; wraps safe path resolution (no `..` escapes).

Errors: on git clone failure or missing dockerfile, raise `RepoContextError`; caller (in Task 6 orchestrator) converts to a submission-level error.

`tests/test_repo_context.py` (integration with tmp dirs; no real git clone — use a local fixture repo):
- Build context from a tiny fixture repo → returns populated `files`, correct `dockerfile_text`, `manifest` parsed or None.
- `read` resolves valid files, rejects `../` paths.
- Missing Dockerfile path → `RepoContextError`.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_repo_context.py -v
```

**Commit:** `feat(detection): RepoContext builder (git clone + manifest parse)`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-5) -->

<!-- START_TASK_4 -->
### Task 4: Starter rule — dockerfile/inline_downloads

**Verifies:** `rac-v1.AC4.1`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/dockerfile/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/dockerfile/inline_downloads.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/detection/test_inline_downloads.py`

**Implementation:**

`inline_downloads.py` is a pure module that exports a `RULE`. The evaluator:
1. Parse Dockerfile via `dockerfile-parse` (`DockerfileParser`) → structured list of instructions with line numbers.
2. Iterate `RUN` instructions. Tokenize each command (handling `\` line continuations and `&&` / `;` separators).
3. For every tokenized subcommand, match against inline-download patterns: `wget <url>`, `curl -O <url>`, `curl -o <path> <url>`, `fetch <url>`, `lwp-request <url>`, `git clone <url>` where `<url>` is http(s). Exclude `curl --data` (not a download) and allow-list certain known-safe URLs (parameterized; empty by default).
4. For each match, emit a `Finding`:
   - `rule_id="dockerfile/inline_downloads"`, `rule_version=1`, `severity="warn"` (design specifies rules start at `warn`).
   - `title="Inline download in Dockerfile"`.
   - `detail` includes the command extracted and the reasoning (reproducibility, supply-chain risk, no integrity check).
   - `line_ranges` pointing at the exact `RUN` line.
   - `suggested_action="override"` (researcher can proceed if this is intentional) but include an `AutoFixAction` for the `apt install` / well-known-package case where we can rewrite to a `COPY + RUN --mount=type=cache` pattern (documented in-rule as a best-effort heuristic; only emitted for a narrow, high-confidence subset, e.g., `wget http://archive.ubuntu.com/...` → swap to `apt install`).

Tests (`test_inline_downloads.py`):
- Dockerfile with `RUN wget https://example.com/install.sh && sh install.sh` → 1 finding with correct line number.
- Dockerfile with `RUN apt-get install -y curl && curl http://corp/blob` → 1 finding on the `curl` subcommand.
- Clean Dockerfile with `RUN pip install flask` → 0 findings.
- Dockerfile with two separate `RUN` lines each containing a `wget` → 2 findings (AC4.6 repeat firing).
- Dockerfile with a `curl --data '{...}' https://api.example/post` → 0 findings (not a download).
- Property test: any Dockerfile text without `wget`/`curl`/`fetch`/`lwp-request`/`git clone` with an http(s) URL produces zero findings.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/detection/test_inline_downloads.py -v
```

**Commit:** `feat(detection): dockerfile/inline_downloads rule`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Starter rules — dockerfile/missing_user, dockerfile/root_user, repo/huge_files_in_git, repo/secrets_in_repo, manifest/undeclared_assets, manifest/unreachable_external

**Verifies:** `rac-v1.AC4.2`, plus broader rule starter set

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/dockerfile/missing_user.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/dockerfile/root_user.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/repo/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/repo/huge_files_in_git.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/repo/secrets_in_repo.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/manifest/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/manifest/undeclared_assets.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/manifest/unreachable_external.py` (NB: unreachability requires network I/O — see note below)
- Create per-rule tests under `apps/control-plane/backend/tests/detection/`

**Implementation:**

All rules are Functional Core and follow the same contract as Task 4.

- `missing_user.py`: fires if Dockerfile has no `USER` instruction (default root). `warn`.
- `root_user.py`: fires if the last `USER` instruction is `root` or `0`. `warn`.
- `huge_files_in_git.py`: iterates `ctx.files`, flags any file ≥ `settings.detection_huge_file_threshold_bytes` (default 50 MB). Emits one finding per such file with `file_path` and `size_bytes` in detail. Property test: monotonic in threshold — lowering the threshold never reduces the finding count.
- `secrets_in_repo.py`: simple regex sweep over `ctx.read()` of text files (≤ 1 MB, detected by extension allowlist `.py`, `.js`, `.ts`, `.go`, `.rs`, `.env`, `.yaml`, `.yml`, `.json`, `.sh`) for common secret patterns: AWS Access Key ID regex, Azure storage key regex, GitHub PAT `ghp_`, private-key PEM headers. Emits `severity="warn"` with line numbers. Do NOT include raw matched secrets in the finding detail — include only a truncated preview of the first 4 characters followed by `***` (e.g., `ghp_***`). This prevents the Control Plane DB from becoming a secondary secret store.
- `undeclared_assets.py` (reads `ctx.manifest`): if manifest declares assets, flag any `mount_path` inside the Dockerfile's `COPY`/`ADD` destinations (asset collision). Fires when the Dockerfile also copies the same path — the researcher likely baked data that should be an asset.
- `unreachable_external.py` — **NB**: the design's `rules/manifest/unreachable_external.py` requires network I/O to verify URLs are reachable. Network I/O in a rule breaks FCIS purity. Resolution: this rule module exports a pure evaluator that only flags *structural* issues (missing sha256, unparseable URL). The actual reachability check is implemented as a separate Shell service (`services/manifest/reachability_check.py` — Phase 8) that emits a synthetic `Finding` via the same `detection_finding` store. Document this split inside the rule module docstring and in `detection/README.md`.

Tests for each rule cover positive cases, negative cases, one property test where applicable, and repeat-firing (AC4.6) where the rule can emit multiple findings.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/detection/ -v
```

**Commit:** `feat(detection): starter rule set (6 rules + tests)`
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 6-8) -->

<!-- START_TASK_6 -->
### Task 6: Detection orchestrator + persistence

**Verifies:** `rac-v1.AC4.3`, `rac-v1.AC4.5`, `rac-v1.AC4.6`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/engine.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/data/detection_finding_store.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_detection_engine.py`

**Implementation:**

`engine.py`: `async def run_detection(session, submission: Submission, workdir: Path) -> list[DetectionFindingRow]`:
1. Build `RepoContext` via `repo_context.build_repo_context`.
2. Load rules (from `app.state.rules`, cached at startup).
3. `findings = evaluate.run_all(rules, ctx)` (pure).
4. For each `Finding`, insert a `detection_finding` row via `detection_finding_store.insert`. Every insert stores `rule_id`, `rule_version`, `severity`, `title`, `detail`, `file_path`, `line_ranges` (JSONB), `auto_fix` (JSONB or null), `created_at`. Decision fields (`decision`, `decision_actor_principal_id`, `decision_at`) default null.
5. If the principal is an agent (`principal.kind == 'agent'`) AND any finding has `severity in ('warn','error')`: transition submission to `needs_user_action` (AC4.5). Emit `approval_event` of kind `detection_needs_user_action`.
6. Return the inserted rows.

`detection_finding_store.py`: append-only writes (matches `AC12.1`; migration from Phase 2 already REVOKEd UPDATE/DELETE — but the `decision` fields still need to be *updated* later by a separate mechanism). **Design deviation:** the design's data-plane schema shows `detection_finding` with nullable `decision` columns. This plan introduces a separate `detection_finding_decision` table instead — to update decisions without granting UPDATE on `detection_finding`, we insert a **separate row** in `detection_finding_decision` whenever a decision is recorded, and the UI/query joins them. This preserves strict append-only (AC12.1). This deviation is approved and documented in `docs/implementation-plans/2026-04-23-rac-v1/README.md` under "Approved design deviations." Create the new table via Alembic migration `0003_detection_finding_decisions.py`: columns `id`, `detection_finding_id FK`, `decision`, `decision_actor_principal_id`, `decision_notes`, `created_at`. Append-only: no UPDATE/DELETE grants.

The decision API (Task 7) writes to `detection_finding_decision`. Queries for the UI LEFT JOIN both tables and return the most recent decision.

Integration in the submission create flow (Phase 2 Task 10 is updated):
- After creating the `submission` row but before dispatching the pipeline, run detection. If any rule fires with severity `error`, the submission is held in `needs_user_action` immediately (Phase 2's fsm doesn't allow this yet — extend `fsm.py` to permit `awaiting_scan → needs_user_action` on a new `TransitionEvent.DetectionNeedsAction` event).
- `warn` findings do NOT block pipeline dispatch; the researcher can accept/override asynchronously in the UI.

`tests/test_detection_engine.py`:
- Interactive user + 0 findings → pipeline dispatched (mocked), state stays `awaiting_scan`.
- Interactive user + 1 warn finding → pipeline dispatched, state `awaiting_scan`, finding row exists.
- Agent + 1 warn finding → state `needs_user_action` (AC4.5), pipeline NOT dispatched.
- Interactive user + 1 error finding → state `needs_user_action`.
- Same rule fires twice on same submission → two `detection_finding` rows (AC4.6 persisted verification).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_detection_engine.py -v
```

**Commit:** `feat(detection): orchestrator + append-only finding + decision stores`
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Findings API — list, decide

**Verifies:** `rac-v1.AC4.3`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/findings.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/schemas/findings.py` (type-only)
- Create: `apps/control-plane/backend/tests/test_findings_api.py`

**Implementation:**

Endpoints:
- `GET /submissions/{id}/findings`: returns list of findings joined with latest decision per finding. Auth: submitter OR approver with matching role OR admin.
- `POST /submissions/{id}/findings/{finding_id}/decisions`: body `{decision: 'accept'|'override'|'auto_fix'|'dismiss', notes?: str}`. Auth: submitter or admin only. Validates decision value, inserts row in `detection_finding_decision` with `decision_actor_principal_id=principal.oid`, returns 201. Returning-to-`awaiting_scan` from `needs_user_action` happens when ALL findings with `severity='error'` have been decided (accept/override/auto_fix — not dismiss) OR when none remain — pure function in `services/detection/resolution.py` decides this.

`services/detection/resolution.py` (pure): `def needs_user_action_resolved(findings_with_decisions: list[FindingWithDecision]) -> bool` — returns True iff every `severity=error` finding has a concrete decision (not null). Property tests: monotonic in decisions; no decisions on severity=warn affect the result.

`tests/test_findings_api.py`:
- Submitter records `accept` on a warn finding → decision row inserted with correct actor; subsequent `GET` shows it.
- AC4.3 verified: assert `decision`, `rule_id`, `rule_version`, `decision_at`, `decision_actor_principal_id` are all persisted.
- Non-submitter/non-admin → 403.
- Deciding the last open `error` finding → submission transitions back to `awaiting_scan` (pipeline re-dispatched via a one-shot retry).
- Invalid decision value → 422.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_findings_api.py -v
```

**Commit:** `feat(control-plane): findings API (list + decide)`
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Nudges UI

**Verifies:** `rac-v1.AC4.1`, `rac-v1.AC4.3`

**Files:**
- Create: `apps/control-plane/frontend/src/features/nudges/nudges-panel.tsx`
- Create: `apps/control-plane/frontend/src/features/nudges/nudge-card.tsx`
- Create: `apps/control-plane/frontend/src/features/nudges/decision-dialog.tsx`
- Create: `apps/control-plane/frontend/src/tests/nudges.test.tsx`

**Implementation:**

UI rendered on the submission detail page:
- For each finding, show a card with title, severity badge, file path + line ranges (linked to a simple source viewer that highlights the line if the file is small enough to embed), detail markdown rendered safely, and a four-button action bar: **Accept**, **Override**, **Apply auto-fix**, **Dismiss**. The Accept/Override/Dismiss buttons open a dialog requesting optional notes, then POST to the decisions endpoint. **Apply auto-fix** is rendered only if `auto_fix` is present; it opens a diff preview and asks for confirmation before POSTing.
- The panel refreshes via TanStack Query invalidation after each decision; the submission's overall status badge reflects whether `needs_user_action` is still active.

Vitest tests: render panel with fixture findings of different shapes; simulate clicks; assert the correct API call; snapshot the dialog UI.

**Verification:**
```bash
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test -- nudges
```

**Commit:** `feat(control-plane): detection nudges UI`
<!-- END_TASK_8 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_TASK_9 -->
### Task 9: End-to-end acceptance pass on AC4

**Verifies:** all Phase 4 ACs (meta)

**Files:** None (verification task)

**Implementation:**

Run integration tests end-to-end. Additionally perform a live smoke test via the dev compose stack:

1. Submit a fixture repo (from `rac-pipeline/golden-repos/clean-python-flask`) after injecting `RUN wget https://example.com/install.sh` into its Dockerfile → UI shows the `inline_downloads` nudge with correct line number (AC4.1). Researcher clicks Override → finding shows decided; pipeline dispatches; submission proceeds.
2. Commit a 100 MB file to a golden repo → `huge_files_in_git` finding with the correct path + size (AC4.2).
3. Submit via `curl` with client credentials → response returns 201 with status `needs_user_action` (AC4.5).
4. Check `detection_finding` and `detection_finding_decision` tables → rule_id, version, decision, timestamps all present (AC4.3).
5. Drop a new file `apps/control-plane/backend/src/rac_control_plane/detection/rules/dockerfile/experimental_rule.py` with a trivial rule that always emits one finding → restart the service (docker compose restart) → submit a new submission → new finding surfaces (AC4.4). Verify no other file was edited to register the rule.
6. Same rule fires twice (e.g., two `wget` on different lines) → two rows in `detection_finding` (AC4.6).

Findings → `phase4-acceptance-report.md` in scratchpad.

**Verification:** commands above.

**Commit:** None.
<!-- END_TASK_9 -->

---

## Phase 4 Done Checklist

- [ ] `detection/` module exists with discovery, evaluator, rules, store
- [ ] 7 starter rules implemented with tests (6 from Task 5 + Task 4)
- [ ] Every rule module is Functional Core and evaluates from `RepoContext` only
- [ ] Rules auto-discover from the package; adding a file requires no edits elsewhere (AC4.4)
- [ ] Agent submissions with findings land in `needs_user_action` (AC4.5)
- [ ] Decisions persist with full audit metadata (AC4.3)
- [ ] Duplicate firings produce distinct rows (AC4.6)
- [ ] Nudges UI renders and records decisions
- [ ] End-to-end acceptance smoke pass recorded
