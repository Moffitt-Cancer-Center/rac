# Phase 3: Build + Scan Pipeline

**Goal:** A GitHub Actions workflow in a separate `rac-pipeline` repo that (a) is triggered by the Control Plane via `repository_dispatch`, (b) clones the researcher repo at a ref, (c) builds and pushes an image to ACR with buildx registry cache, (d) generates a Syft SBOM, (e) runs Grype scan, (f) polls Defender for Containers for its verdict on the pushed digest, (g) consolidates findings against `SCAN_SEVERITY_GATE`, (h) uploads artifacts to Blob, (i) HMAC-signs a callback to the Control Plane. Control Plane receives the callback, validates HMAC, writes `scan_result`, and advances `submission.status` via the FSM.

**Architecture:** Pipeline lives in a separate repo so researcher-authored Dockerfiles never run inside RAC's main repo. Dispatch uses `repository_dispatch` with a signed payload containing submission ID, repo coordinates, and a short-lived HMAC callback secret. Auth from GHA to Azure is OIDC federated identity (no SP secrets). Consolidation + severity gate is a Functional Core Python module in `rac-pipeline/scripts/` with property-based tests on the pure logic. Callback signing is GitHub-style `X-Hub-Signature-256` SHA-256 HMAC with 5-minute timestamp window. Control Plane callback handler is Imperative Shell wrapping a Functional Core `verify_callback` module.

**Tech Stack:** GitHub Actions (reusable workflow), Docker Buildx (ACR registry cache, `mode=max`), Syft (anchore/syft v1.42+), Grype (anchore/grype latest), Azure Resource Graph API (via `az graph query` CLI) for Defender polling, Azure Blob Storage CLI upload via OIDC, Python 3.12 scripts for consolidation + callback. Control Plane: FastAPI route + `hmac.compare_digest`, `httpx` outbound for pipeline dispatch.

**Scope:** Phase 3 of 8.

**Codebase verified:** 2026-04-23 — `/home/sysop/rac-pipeline/` does NOT exist; it is created in Task 1 of this phase. `apps/control-plane/` exists from Phase 2 with submission CRUD and a stubbed pipeline dispatch. `Microsoft.Security` + `Defender for Containers` plan is enabled during Tier 1 bootstrap (documented in Phase 1 runbook).

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC3: Submission is API-first (webhook portion)
- **rac-v1.AC3.3 Success:** A registered webhook subscriber receives an HMAC-signed callback for every submission state transition matching its subscribed event types.
- **rac-v1.AC3.4 Failure:** Pipeline callback with an invalid HMAC signature is rejected with 401 and the submission state is unchanged.
- **rac-v1.AC3.6 Edge:** A webhook subscription auto-disables after a configurable number of consecutive delivery failures; operator sees this in the admin UI.

### rac-v1.AC5: Build + scan pipeline enforces the severity gate
- **rac-v1.AC5.1 Success:** A clean golden repo builds, is pushed to ACR, passes both Grype and Defender, and the submission advances to `awaiting_research_review`.
- **rac-v1.AC5.2 Failure:** A golden repo with a planted HIGH-severity CVE in a dependency is blocked; submission transitions to `scan_rejected`; researcher sees the specific CVE list in the UI.
- **rac-v1.AC5.3 Failure:** A repo with an invalid Dockerfile transitions to `pipeline_error` with the build log artifact accessible from the submission detail view.
- **rac-v1.AC5.4 Edge:** A Defender scan that exceeds its timeout produces a partial verdict; submission advances with an explicit "Defender scan pending" warning badge surfaced to the IT approver.
- **rac-v1.AC5.5 Edge:** The layer cache is hit on a second build of an unchanged repo (evidenced by cache-hit messages in the build log or substantially reduced wall-clock time).

### rac-v1.AC2.2 (partial — the scan-passing → awaiting_research_review transition)
- **rac-v1.AC2.2 Success (partial):** A submission progresses through `awaiting_scan → awaiting_research_review` when the scan passes (full FSM verified in Phase 5).

### rac-v1.AC2.5 (partial — pipeline failure path)
- **rac-v1.AC2.5 Failure:** Submission with a missing Dockerfile at the declared path transitions to `pipeline_error` or `scan_rejected` (per pipeline failure mode) with a researcher-visible error message.

### rac-v1.AC10: Observability — scan verdict metric (partial)
- **rac-v1.AC10.2 Success (partial):** Custom metric `rac.scans.verdict` (counter, labeled by `verdict`) is emitted via OpenTelemetry at callback ingestion whenever the pipeline delivers a result. Increments for `passed`, `partial_passed`, `rejected`, `partial_rejected`, `build_failed`.

**Verifies:** Functionality phase (pipeline behavior + callback behavior). Tasks list which AC cases they cover.

---

## File Classification Policy

- GitHub Actions YAML files: exempt.
- Python scripts in `rac-pipeline/scripts/`: MUST be classified. Pure consolidation/severity logic → Functional Core. CLI entrypoints, blob uploads, subprocess calls → Imperative Shell. Keep them in separate files.
- Control Plane additions: FCIS per Phase 2.
- Dockerfiles and golden-repo fixtures under `rac-pipeline/golden-repos/`: exempt.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Create sibling rac-pipeline repo scaffold

**Verifies:** None (setup)

**Files:**
- Create directory: `/home/sysop/rac-pipeline/` (sibling to `/home/sysop/rac/`)
- Create: `/home/sysop/rac-pipeline/README.md`
- Create: `/home/sysop/rac-pipeline/.gitignore`
- Create: `/home/sysop/rac-pipeline/pyproject.toml`
- Create: `/home/sysop/rac-pipeline/.github/CODEOWNERS`
- Create: `/home/sysop/rac-pipeline/scripts/__init__.py`
- Create: `/home/sysop/rac-pipeline/tests/__init__.py`
- Create: `/home/sysop/rac-pipeline/golden-repos/README.md`

**Implementation:**

`rac-pipeline` is a separate GitHub repository; during local development we create it as a sibling directory, init git, and push to a new remote later. The executor should confirm with the operator whether to create the GitHub remote now (requires repo-create permissions) or leave that for operator bootstrap.

`README.md`: explains what this repo is (the build + scan worker for RAC), links back to the main RAC repo, lists prerequisites (GHA enabled, OIDC federated credential configured, ACR access).

`pyproject.toml`: Python 3.12, dependencies (`requests`, `pydantic>=2.7`, `click` for script CLIs), dev deps (`pytest`, `hypothesis`, `ruff`, `mypy`). No FastAPI / SQLAlchemy — this repo is scripts only.

Initialize git: `cd /home/sysop/rac-pipeline && git init && git add . && git commit -m "chore: initial scaffold"`. No remote yet.

**Verification:**
```bash
test -d /home/sysop/rac-pipeline/.git
cd /home/sysop/rac-pipeline && uv sync && uv run pytest --collect-only
```

**Commit:** (inside `rac-pipeline` repo) `chore: initial scaffold`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Severity consolidation logic (Functional Core)

**Verifies:** `rac-v1.AC5.1`, `rac-v1.AC5.2`, `rac-v1.AC5.4`

**Files:**
- Create: `/home/sysop/rac-pipeline/scripts/consolidation.py` (pattern: Functional Core)
- Create: `/home/sysop/rac-pipeline/scripts/schemas.py` (type-only; Pydantic models for Grype JSON, Defender ARG response, consolidated verdict)
- Create: `/home/sysop/rac-pipeline/tests/test_consolidation.py`

**Implementation:**

`schemas.py`: Pydantic models for:
- `GrypeFinding`: `cve_id`, `severity` (Literal), `cvss_score: float | None`, `epss_score: float | None`, `is_kev: bool`, `package_name`, `package_version`, `fixed_version: str | None`.
- `DefenderFinding`: parallel shape, sourced from ARG query result.
- `ConsolidatedFinding`: dedup'd, with `sources: list[Literal['grype','defender']]`, `effective_severity`, `severity_reason` (e.g., `"KEV override"`, `"EPSS ≥ 0.95 bumped to HIGH"`).
- `Verdict`: Literal `["passed", "rejected", "partial_passed", "partial_rejected", "build_failed"]`.
- `ConsolidatedReport`: `verdict`, `effective_severity`, `findings: list[ConsolidatedFinding]`, `grype_count`, `defender_count`, `defender_timed_out: bool`, `build_log_uri`, `sbom_uri`, `grype_report_uri`, `defender_report_uri`.

`consolidation.py` (pure):
- `def _sev_rank(s: Severity) -> int` → `{"none":0,"low":1,"medium":2,"high":3,"critical":4}`.
- `def merge_findings(grype: list[GrypeFinding], defender: list[DefenderFinding]) -> list[ConsolidatedFinding]` — dedup by `cve_id` + `package_name`; merge sources; use `max(_sev_rank)` for base severity; apply KEV override (any source has `is_kev=True` → bump to `critical` with reason); apply EPSS threshold (`epss_score >= 0.95` and current < `high` → bump to `high`).
- `def evaluate_gate(findings: list[ConsolidatedFinding], gate: Severity, defender_timed_out: bool) -> Verdict`:
  - `effective = max(f.effective_severity for f in findings) if findings else "none"`.
  - If `_sev_rank(effective) >= _sev_rank(gate)`: verdict `"rejected"` (or `"partial_rejected"` if `defender_timed_out`).
  - Else: verdict `"passed"` (or `"partial_passed"` if `defender_timed_out`).

Property-based tests (`ed3d-house-style:property-based-testing` applies):
- Monotonicity: adding a finding never decreases `effective_severity`.
- KEV override is absorbing: any KEV finding ⇒ `effective_severity == critical`.
- `evaluate_gate(x, gate)` with gate strictly above every finding's severity ⇒ always `"passed"` (or `partial_passed`).
- `merge_findings` is commutative in the Grype/Defender argument positions (sorting the output).
- Examples from AC5.1 (clean → passed), AC5.2 (planted HIGH → rejected), AC5.4 (Defender timeout with otherwise-clean Grype → `partial_passed`).

**Verification:**
```bash
cd /home/sysop/rac-pipeline
uv run pytest tests/test_consolidation.py -v
```

**Commit:** (in rac-pipeline) `feat: severity consolidation pure logic + property tests`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-5) -->

<!-- START_TASK_3 -->
### Task 3: Pipeline I/O shell — Defender polling, Blob uploads, HMAC callback

**Verifies:** `rac-v1.AC3.3`, `rac-v1.AC3.4`, `rac-v1.AC5.4`

**Files:**
- Create: `/home/sysop/rac-pipeline/scripts/defender_poll.py` (pattern: Imperative Shell)
- Create: `/home/sysop/rac-pipeline/scripts/grype_runner.py` (pattern: Imperative Shell)
- Create: `/home/sysop/rac-pipeline/scripts/syft_runner.py` (pattern: Imperative Shell)
- Create: `/home/sysop/rac-pipeline/scripts/blob_upload.py` (pattern: Imperative Shell)
- Create: `/home/sysop/rac-pipeline/scripts/callback.py` (pattern: Imperative Shell)
- Create: `/home/sysop/rac-pipeline/scripts/hmac_sign.py` (pattern: Functional Core)
- Create: `/home/sysop/rac-pipeline/tests/test_hmac_sign.py`
- Create: `/home/sysop/rac-pipeline/tests/test_defender_poll.py`

**Implementation:**

`hmac_sign.py` (pure): `def compute_signature(secret: bytes, timestamp: str, body: bytes) -> str` returns `"sha256=" + hmac.sha256(secret, f"{timestamp}.".encode() + body).hexdigest()`. `def verify_signature(expected_header: str, secret: bytes, timestamp: str, body: bytes, max_age_seconds: int = 300) -> None` raises `SignatureInvalid` if mismatch or age exceeds window. Uses `hmac.compare_digest` for constant-time comparison.

`defender_poll.py` (shell): `async def poll_defender(image_digest: str, timeout_seconds: int = 14400) -> tuple[list[DefenderFinding], bool]`. Implementation:
1. Call `az graph query -q "..."` via subprocess (or Azure Resource Graph SDK if feasible). Query uses `securityresources | where type == 'microsoft.security/assessments/subassessments' | where properties.id contains $image_digest` to fetch finding subassessments. Parse results into `DefenderFinding` objects.
2. Loop with exponential backoff (start 60s, cap 300s) until findings returned OR assessment status `Completed`.
3. On `timeout_seconds` elapsed without completion → return `(partial_findings, timed_out=True)`.
4. Return parsed findings and a bool indicating whether the timeout was hit.

`grype_runner.py` (shell): subprocess `grype <image-ref> -o json`, parse into `GrypeFinding[]` via `schemas.GrypeFinding`.

`syft_runner.py` (shell): subprocess `syft <image-ref> -o cyclonedx-json=sbom.cdx.json`, return `Path` to the SBOM file.

`blob_upload.py` (shell): `async def upload_file(account_url: str, container: str, blob_path: str, local_path: Path) -> str` using `azure-storage-blob` with `DefaultAzureCredential` (GHA OIDC federated). Returns the blob URL.

`callback.py` (shell): `async def post_callback(url: str, secret: bytes, body: dict) -> None`. Serializes `body` to bytes (canonical JSON — sorted keys, no extra whitespace), computes signature with current timestamp, POSTs with headers `X-RAC-Timestamp: <iso8601>`, `X-RAC-Signature-256: sha256=<hex>`, `Content-Type: application/json`. Retries up to 3 times on 5xx with exponential backoff.

`tests/test_hmac_sign.py` (pure):
- Roundtrip: `verify_signature(compute_signature(secret, ts, body), secret, ts, body)` succeeds.
- Modified body → `SignatureInvalid`.
- Modified secret → `SignatureInvalid`.
- Timestamp older than `max_age_seconds` → `SignatureInvalid`.
- Property test (Hypothesis): any `(secret, ts, body)` triple with matching signature verifies; any mismatch fails.

`tests/test_defender_poll.py` (mock subprocess `az graph query` output):
- Findings present → returns parsed list, `timed_out=False`.
- Empty initial response, then populated response after one loop → returns findings.
- Exceeds timeout → returns whatever was seen with `timed_out=True`.

**Verification:**
```bash
cd /home/sysop/rac-pipeline
uv run pytest -v
```

**Commit:** (in rac-pipeline) `feat: pipeline I/O shell modules + HMAC signing`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Pipeline orchestration script (entrypoint)

**Verifies:** `rac-v1.AC5.1`, `rac-v1.AC5.2`, `rac-v1.AC5.3`, `rac-v1.AC5.4`

**Files:**
- Create: `/home/sysop/rac-pipeline/scripts/pipeline.py` (pattern: Imperative Shell)
- Create: `/home/sysop/rac-pipeline/tests/test_pipeline_e2e.py` (uses mocked subprocess + mocked Azure APIs)

**Implementation:**

`pipeline.py` is the GHA-invoked entrypoint. Click CLI with flags: `--submission-id`, `--repo-url`, `--git-ref`, `--dockerfile-path`, `--image-tag`, `--callback-url`, `--callback-secret-env`, `--severity-gate`, `--blob-account-url`, `--blob-container`.

Steps:
1. `git clone --depth 1 --branch <ref> <repo_url> /workspace/repo` (subprocess; on failure → verdict `build_failed`).
2. Verify `<dockerfile_path>` exists at checkout. Missing → verdict `build_failed` with reason `dockerfile_not_found` (AC5.3 + AC2.5).
3. `docker buildx build --platform linux/amd64 --cache-from type=registry,ref=<acr>/<slug>:cache --cache-to type=registry,ref=<acr>/<slug>:cache,mode=max --tag <image-tag> --push --file <dockerfile_path> /workspace/repo 2>&1 | tee /workspace/build.log`. Failure → verdict `build_failed` (AC5.3). Log is uploaded unconditionally at the end.
4. Resolve image digest: `docker buildx imagetools inspect <image-tag> --format '{{json .}}'` → parse digest.
5. `syft_runner.run(image_tag)` → `sbom.cdx.json`; upload to Blob.
6. `grype_runner.run(image_tag)` → parse findings.
7. `defender_poll.poll_defender(digest, timeout=settings.defender_timeout_seconds)` → findings + `timed_out`.
8. `merged = consolidation.merge_findings(grype_findings, defender_findings)`.
9. `verdict = consolidation.evaluate_gate(merged, gate=severity_gate, defender_timed_out=timed_out)`.
10. Upload build log, Grype JSON, Defender JSON, SBOM to Blob. Capture URIs.
11. Construct callback body: `{submission_id, verdict, effective_severity, findings: [...], build_log_uri, sbom_uri, grype_report_uri, defender_report_uri, image_digest, image_ref}`.
12. `callback.post_callback(callback_url, secret_bytes, body)`.

`tests/test_pipeline_e2e.py`: parametrized test using mocked `docker`, `grype`, `syft`, and `az graph query` subprocesses. Scenarios:
- Clean golden repo → verdict `passed`, callback body has `effective_severity="none"` (or `low`).
- Planted HIGH CVE → verdict `rejected` with the CVE in `findings`.
- Invalid Dockerfile → verdict `build_failed`, reason, build log uploaded.
- Defender timeout with clean Grype → verdict `partial_passed`, `defender_timed_out=True`.

**Verification:**
```bash
cd /home/sysop/rac-pipeline
uv run pytest tests/test_pipeline_e2e.py -v
```

**Commit:** (in rac-pipeline) `feat: pipeline orchestration entrypoint`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: GitHub Actions reusable workflow

**Verifies:** `rac-v1.AC5.1`, `rac-v1.AC5.2`, `rac-v1.AC5.3`, `rac-v1.AC5.4`, `rac-v1.AC5.5`

**Files:**
- Create: `/home/sysop/rac-pipeline/.github/workflows/build-and-scan.yml`
- Create: `/home/sysop/rac-pipeline/.github/workflows/golden-repos-nightly.yml`

**Implementation:**

`build-and-scan.yml`: triggered by `repository_dispatch` with event type `rac_submission`. Single job running on `ubuntu-latest`. Permissions: `id-token: write`, `contents: read`.

```yaml
on:
  repository_dispatch:
    types: [rac_submission]
  workflow_call:
    inputs:
      submission_id: { type: string, required: true }
      # ... same set

permissions:
  id-token: write
  contents: read

jobs:
  build-and-scan:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - uses: docker/setup-buildx-action@v3
      - name: ACR login
        run: az acr login --name ${{ vars.ACR_NAME }}
      - name: Install scanners
        run: |
          curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin
          curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin
      - name: Fetch callback secret from Key Vault
        id: kv
        run: |
          SECRET=$(az keyvault secret show --vault-name ${{ vars.KV_NAME }} --name ${{ github.event.client_payload.callback_secret_name }} --query value -o tsv)
          echo "::add-mask::$SECRET"
          echo "value=$SECRET" >> $GITHUB_OUTPUT
      - name: Run pipeline
        env:
          CALLBACK_SECRET: ${{ steps.kv.outputs.value }}
          ACR_NAME: ${{ vars.ACR_NAME }}
          ACR_LOGIN_SERVER: ${{ vars.ACR_LOGIN_SERVER }}
          BLOB_ACCOUNT_URL: ${{ vars.BLOB_ACCOUNT_URL }}
          BLOB_CONTAINER: scan-artifacts
          SEVERITY_GATE: ${{ vars.SEVERITY_GATE }}
        run: |
          uv run scripts/pipeline.py \
            --submission-id "${{ github.event.client_payload.submission_id }}" \
            --repo-url "${{ github.event.client_payload.repo_url }}" \
            --git-ref "${{ github.event.client_payload.git_ref }}" \
            --dockerfile-path "${{ github.event.client_payload.dockerfile_path }}" \
            --image-tag "${{ vars.ACR_LOGIN_SERVER }}/${{ github.event.client_payload.slug }}:${{ github.event.client_payload.submission_id }}" \
            --callback-url "${{ github.event.client_payload.callback_url }}"
      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: scan-${{ github.event.client_payload.submission_id }}
          path: /workspace/*.log /workspace/*.json
          retention-days: 30
```

`golden-repos-nightly.yml`: cron `0 4 * * *` running the reusable workflow against each golden repo fixture (AC5.5 verified here by comparing `cache-miss` vs `cache-hit` wall-clock times across two back-to-back runs).

**Verification:**
```bash
# Lint
npx --yes action-validator .github/workflows/build-and-scan.yml
# Full acceptance via golden-repos-nightly job in CI — manual trigger via `workflow_dispatch` after the first deploy.
```

**Commit:** (in rac-pipeline) `feat: GHA reusable workflow for build + scan`
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 6-7) -->

<!-- START_TASK_6 -->
### Task 6: Golden-repo fixtures

**Verifies:** `rac-v1.AC5.1`, `rac-v1.AC5.2`, `rac-v1.AC5.3`, `rac-v1.AC5.5`

**Files:**
- Create: `/home/sysop/rac-pipeline/golden-repos/clean-python-flask/Dockerfile`
- Create: `/home/sysop/rac-pipeline/golden-repos/clean-python-flask/app.py`
- Create: `/home/sysop/rac-pipeline/golden-repos/clean-python-flask/requirements.txt`
- Create: `/home/sysop/rac-pipeline/golden-repos/planted-cve-node/Dockerfile`
- Create: `/home/sysop/rac-pipeline/golden-repos/planted-cve-node/package.json`
- Create: `/home/sysop/rac-pipeline/golden-repos/invalid-dockerfile/Dockerfile` (intentionally broken)
- Create: `/home/sysop/rac-pipeline/golden-repos/large-repo/Dockerfile` (for cache-hit measurement)
- Create: `/home/sysop/rac-pipeline/golden-repos/README.md`

**Implementation:**

Each subdir is a minimal-but-realistic researcher app:

- `clean-python-flask/`: Flask 3.x, Python 3.12, no known HIGH/CRITICAL CVEs (pinned to current clean versions). Dockerfile uses `python:3.12-slim`, installs `gunicorn`, copies `app.py`, sets `USER 10001`, runs gunicorn.
- `planted-cve-node/`: Node 20 with a `package.json` pinning `log4js@6.4.0` (or another package with a known public HIGH CVE — verify with `grype` before committing). Document the planted CVE in a `KNOWN_CVES.md` so the test expectation stays stable.
- `invalid-dockerfile/`: Dockerfile with syntax error (e.g., `FROM` missing argument, or RUN step that fails).
- `large-repo/`: Dockerfile that copies many files and installs many deps, giving buildx a non-trivial layer set for cache-hit timing (AC5.5).

`README.md` explains each fixture's purpose + expected pipeline outcome + which AC it covers.

**Verification:**
```bash
cd /home/sysop/rac-pipeline/golden-repos/clean-python-flask
docker build -t test-clean .  # succeeds
grype test-clean  # zero HIGH/CRITICAL findings
cd ../planted-cve-node
docker build -t test-planted .
grype test-planted  # at least one HIGH finding visible
```

**Commit:** (in rac-pipeline) `feat: golden-repo fixtures (clean, planted-cve, invalid, large)`
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Control Plane pipeline dispatcher (service-to-service)

**Verifies:** `rac-v1.AC5.1`, `rac-v1.AC5.4` (the dispatch half)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/github.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/payload.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/pipeline_dispatch/secret_mint.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_pipeline_dispatch.py`

**Implementation:**

`payload.py` (pure): `def build_dispatch_payload(submission, callback_base_url: str, callback_secret_name: str) -> dict` constructs the `client_payload` object the GHA workflow expects (`submission_id`, `repo_url`, `git_ref`, `dockerfile_path`, `slug`, `callback_url`, `callback_secret_name`). Keep it a single function so tests can inspect it.

`secret_mint.py` (shell): `async def mint_callback_secret(submission_id: UUID) -> str`:
1. Generate 32 bytes of `os.urandom`; hex-encode.
2. Store in Key Vault with name `rac-pipeline-cb-{submission_id}`, content type `text/plain`, expiry = now + `2 * settings.pipeline_timeout_minutes` minutes (per design — 2× pipeline timeout).
3. Return the secret name (not the value).
The secret value is sent to the GHA workflow via a Key Vault fetch in the workflow (Task 5) — the caller only knows the name.

`github.py` (shell): `async def dispatch(payload: dict) -> None`:
1. POST to `https://api.github.com/repos/{owner}/{pipeline_repo}/dispatches` with body `{"event_type":"rac_submission","client_payload":payload}`.
2. Auth: GitHub App installation token (preferred) via `settings.gh_app_id`, `settings.gh_app_private_key` (SecretStr, stored in Key Vault, fetched at startup). Fallback: PAT in dev.
3. Validate payload size ≤ 10 KB per GH limit (the skill investigation noted this); raise `ValidationApiError` if exceeded.
4. On 422 / 404 → surface a clear server-side error but DO NOT surface raw GitHub error body to the user.

Wire dispatch into the submission flow: after `create_submission` successfully writes the row (from Phase 2 Task 10), enqueue a dispatch via a background task (`asyncio.create_task` or FastAPI `BackgroundTasks`) so the HTTP response to the researcher is not blocked on GitHub latency. Errors are logged; submission stays `awaiting_scan` and a separate retry path (admin UI button in Phase 5) will resubmit.

Update Phase 2 Task 10 behavior: the submission service now calls `mint_callback_secret` then `build_dispatch_payload` then `github.dispatch` after creating the row. If dispatch fails outright (retryable 5xx or network error), still return 201 to the user but log; if dispatch raises a 4xx (e.g., payload too large), abort the submission by marking the row `pipeline_error` and returning 422 to the user.

`tests/test_pipeline_dispatch.py`: 
- Pure test: `build_dispatch_payload` produces the exact dict shape.
- Integration with `respx` mocking GitHub API: successful dispatch → no error; 422 → submission row ends in `pipeline_error`; 5xx → background retry logged.
- Payload size limit: mock a large manifest in the submission → payload > 10 KB → raises without dispatching.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_pipeline_dispatch.py -v
```

**Commit:** (in rac) `feat(control-plane): pipeline dispatch service`
<!-- END_TASK_7 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_SUBCOMPONENT_D (tasks 8-9) -->

<!-- START_TASK_8 -->
### Task 8: Control Plane webhook callback endpoint + outbound webhook deliveries

**Verifies:** `rac-v1.AC3.3`, `rac-v1.AC3.4`, `rac-v1.AC3.6`, `rac-v1.AC5.1`, `rac-v1.AC5.2`, `rac-v1.AC5.3`, `rac-v1.AC5.4`, `rac-v1.AC2.2` (partial), `rac-v1.AC2.5` (partial)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/webhooks.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/webhooks/verify.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/webhooks/deliver.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/webhooks/sign.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/scan_results/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/scan_results/ingest.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_webhook_callback.py`
- Create: `apps/control-plane/backend/tests/test_webhook_deliver.py`

**Implementation:**

**Inbound callback** (`POST /webhooks/pipeline-callback/{submission_id}`):

`services/webhooks/verify.py` (pure): same pair as `hmac_sign.py` in rac-pipeline — `compute_signature`, `verify_signature` — kept in Control Plane to avoid a shared dependency. Tests here mirror the rac-pipeline tests.

`api/routes/webhooks.py`: handler reads raw body bytes (not parsed JSON — signature verifies the exact bytes), extracts headers `X-RAC-Timestamp`, `X-RAC-Signature-256`, fetches the submission's callback secret from Key Vault using the stored secret name, verifies. On failure → 401 (AC3.4). On success:
1. Parse body as `PipelineCallback` Pydantic model.
2. Call `scan_results.ingest(session, submission_id, callback)`.
3. Return 200 with empty body.

`services/scan_results/ingest.py`:
1. Insert `scan_result` row with all findings, artifact URIs, verdict, `image_digest`, `effective_severity`, `defender_timed_out`.
2. Compute next submission status via pure `fsm.transition`: verdict → event → new status. Mapping:
   - `passed` or `partial_passed` → `TransitionEvent.ScanPassed` → `awaiting_research_review` (AC5.1, AC5.4).
   - `rejected` or `partial_rejected` → `TransitionEvent.SevGateFailed` → `scan_rejected` (AC5.2).
   - `build_failed` → `TransitionEvent.PipelineError` → `pipeline_error` (AC5.3, AC2.5).
3. Update `submission.status`, write `approval_event` (kind=`scan_completed`).
4. Emit `rac.scans.verdict` counter: `metrics.scan_verdict_counter.add(1, {"verdict": callback.verdict})`. Declare `scan_verdict_counter` in `rac_control_plane/metrics.py` (Phase 2 Task 13B) — add it there alongside the existing submission counter. This task only wires the call site. (AC10.2 partial)
5. Enqueue outbound webhook deliveries for any `webhook_subscription` row with matching `event_types`.
6. Purge the submission's callback secret from Key Vault (it's single-use).

**Outbound webhooks** (`AC3.3`, `AC3.6`):

`services/webhooks/sign.py` (pure): same `compute_signature` contract as inbound; separate function so outbound signing uses the per-subscription secret.

`services/webhooks/deliver.py`: `async def deliver_event(session, event)`:
1. Find matching subscriptions (`event_types` array contains `event.kind`, `enabled=true`).
2. For each: sign the canonical JSON body with the subscription's HMAC secret (Key Vault), POST to `subscription.callback_url` with headers `X-RAC-Event-Type`, `X-RAC-Timestamp`, `X-RAC-Signature-256`.
3. Retry on 5xx/network errors (exponential backoff, max 5 attempts).
4. On final failure, increment `subscription.consecutive_failures`; if ≥ `settings.webhook_max_consecutive_failures` (default 10), set `subscription.enabled=false` and insert an `approval_event` of kind `webhook_auto_disabled` so it appears in the admin UI (AC3.6).
5. On success, reset `consecutive_failures=0`.

Emitted events in Phase 3: `submission.scan_completed`, `submission.status_changed` (with `from`/`to`). Further events added in Phase 5.

`tests/test_webhook_callback.py`:
- Valid HMAC + verdict `passed` → submission transitions to `awaiting_research_review`, `scan_result` row inserted (AC5.1, AC2.2 partial).
- Valid HMAC + verdict `rejected` → `scan_rejected`, findings visible via `GET /submissions/{id}` (AC5.2).
- Valid HMAC + verdict `build_failed` → `pipeline_error`, build log URI recorded (AC5.3, AC2.5).
- Valid HMAC + verdict `partial_passed` → `awaiting_research_review` with `defender_timed_out=true` flag exposed in response (AC5.4).
- Invalid HMAC → 401, submission status unchanged (AC3.4).
- Expired timestamp → 401.
- Replay (same body, correct HMAC, stale timestamp > 5 min) → 401.

`tests/test_webhook_deliver.py`:
- Subscription with matching event type → callback delivered with valid signature that the subscriber can verify (AC3.3). Use `respx` to assert request body + headers.
- Subscription with non-matching event type → not delivered.
- Subscriber returns 500 N times → `consecutive_failures` increments; after threshold → `enabled=false`, `approval_event` row exists (AC3.6).
- Subscriber returns 200 → `consecutive_failures` reset.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_webhook_callback.py apps/control-plane/backend/tests/test_webhook_deliver.py -v
```

**Commit:** (in rac) `feat(control-plane): pipeline callback ingestion + outbound webhooks`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: Admin UI additions — webhook subscriptions + scan results view

**Verifies:** `rac-v1.AC3.6`, `rac-v1.AC5.2` (UI surfacing)

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/webhook_subscriptions.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/frontend/src/features/admin/webhook-subscriptions/index.tsx`
- Create: `apps/control-plane/frontend/src/features/submissions/scan-findings-view.tsx`
- Create: `apps/control-plane/backend/tests/test_webhook_subscriptions_api.py`

**Implementation:**

Backend: CRUD endpoints for `webhook_subscription` (admin-only): `POST`, `GET`, `PATCH` (toggle `enabled`, update `event_types`, reset `consecutive_failures`), `DELETE`. On `POST`, mint a new HMAC secret via Key Vault, return the secret once (never again), store the secret name on the row.

Frontend:
- `/admin/webhook-subscriptions` — table of subscriptions with `enabled`, `consecutive_failures`, last delivery attempt; one-shot secret reveal on creation.
- `/submissions/{id}` scan results tile: for `scan_rejected` submissions, render the CVE list sorted by severity with package name, version, fix-version, KEV badge, EPSS score. For `pipeline_error`, show the `build_log_uri` with a "Download build log" link.

Tests: CRUD integration tests; the scan-findings React component has a snapshot test for each verdict shape.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_webhook_subscriptions_api.py -v
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test
```

**Commit:** (in rac) `feat(control-plane): webhook subscription admin + scan findings view`
<!-- END_TASK_9 -->

<!-- END_SUBCOMPONENT_D -->

<!-- START_TASK_9B -->
### Task 9B: Webhook HMAC secret rotation (scheduled job)

**Verifies:** `rac-v1.AC3.3` (rotation doesn't break live subscriptions; new secret is active on next delivery)

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/settings.py` (add `webhook_secret_grace_period_hours: int = 24` and `internal_job_secret: SecretStr` to the Settings class)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/webhooks/rotate_secrets.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/jobs.py` (pattern: Imperative Shell) — ACA scheduled-job endpoint; extend in later phases
- Create: `apps/control-plane/backend/tests/test_webhook_rotation.py`

**Implementation:**

Webhook subscriptions store the HMAC signing secret as a Key Vault secret name on the `webhook_subscription` row. Over time those secrets age. The rotation job:

1. Queries all `enabled=true` `webhook_subscription` rows where `secret_rotated_at < NOW() - INTERVAL '<rotation_days> days'`. `rotation_days` = `settings.webhook_secret_rotation_days` (default 30, matching Phase 2's `settings.py` declaration).
2. For each subscription:
   a. Generate a new 32-byte random HMAC secret.
   b. Create a new Key Vault secret version at the same secret name (`az keyvault secret set` atomically replaces the current value and creates a new version). Key Vault retains the previous version for `settings.webhook_secret_grace_period_hours` (default 24 hours) before the old version is disabled — this means in-flight deliveries using the old secret are still valid during the grace window.
   c. Set `subscription.secret_rotated_at = NOW()`.
3. Log each rotation as a structlog INFO event (correlation ID attached). Do not emit an `approval_event` — rotation is not a submission state change.

`api/routes/jobs.py`: a `POST /internal/jobs/rotate-webhook-secrets` endpoint, accessible only via an `X-Internal-Auth` header secret configured in `settings.internal_job_secret` (a shared secret injected as ACA secret). This endpoint is called by an **ACA scheduled job** (CRON expression in the ACA job definition, not GHA). Document the ACA job definition parameters in a comment at the top of the module.

ACA scheduled job trigger: `0 2 * * *` (2 AM UTC daily). ACA Jobs do not need a persistent container — they run to completion. The job container is the same Control Plane image; the job command is `POST /internal/jobs/rotate-webhook-secrets` via `curl`.

`tests/test_webhook_rotation.py`:
- Setup: 3 subscriptions; 2 with `secret_rotated_at` past threshold, 1 recent.
- Call rotation function; assert 2 subscriptions have updated `secret_rotated_at`, 1 is unchanged.
- Assert Key Vault mock was called to create new secret versions for the 2 aged subscriptions.
- Assert that a delivery attempt made with the new secret after rotation succeeds (use `verify_signature` to confirm the new secret validates).
- Assert that the old-secret delivery attempt still verifies within the grace window (simulate by verifying with the previous version's value; this is an in-memory test — document that Key Vault version retention requires the ACA job to leave the previous version active for `grace_period_hours`).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_webhook_rotation.py -v
```

**Commit:** `feat(control-plane): webhook HMAC secret rotation scheduled job (AC3.3)`
<!-- END_TASK_9B -->

<!-- START_TASK_10 -->
### Task 10: End-to-end acceptance on golden repos

**Verifies:** all Phase 3 ACs (meta)

**Files:** None (verification task)

**Implementation:**

Run `golden-repos-nightly.yml` workflow manually via `workflow_dispatch`. Each golden repo triggers the full pipeline including callback to a dev Control Plane. Verify:

- `clean-python-flask` → submission ends at `awaiting_research_review`, `scan_result.verdict='passed'`, no HIGH/CRITICAL findings in `findings` payload (AC5.1).
- `planted-cve-node` → submission ends at `scan_rejected`, `scan_result.verdict='rejected'`, CVE list contains the planted CVE (AC5.2).
- `invalid-dockerfile` → submission ends at `pipeline_error`, `build_log_uri` points at accessible blob URL (AC5.3).
- Second run of `clean-python-flask` without changes → build wall-clock < 50% of the first run (indicating buildx cache hits; AC5.5).
- Simulate Defender timeout by setting `defender_timeout_seconds=1` on a staging config → verdict `partial_passed`, IT approver UI shows the warning (AC5.4).

Save findings to scratchpad as `phase3-acceptance-report.md`.

**Verification:** commands above; must pass all 5 AC5 cases.

**Commit:** None.
<!-- END_TASK_10 -->

---

## Phase 3 Done Checklist

- [ ] `rac-pipeline` repo exists with pipeline scripts and tests passing
- [ ] GHA `build-and-scan.yml` workflow passes syntax validation
- [ ] Golden-repo fixtures exist; Grype behavior verified locally
- [ ] Control Plane dispatches via GitHub API on submission create
- [ ] Control Plane accepts signed callbacks; invalid signatures are rejected 401
- [ ] Submission state transitions correctly on each verdict
- [ ] Webhook subscriptions deliver signed events; auto-disable works
- [ ] Webhook HMAC secret rotation job wired; test passes (Task 9B)
- [ ] `rac.scans.verdict` counter emitted at callback ingestion (AC10.2 partial)
- [ ] End-to-end golden-repo acceptance pass across all AC5 cases
- [ ] FCIS classification on every non-exempt file
