# Phase 8: Asset handling and manifest parsing

**Goal:** `rac.yaml` is parsed from submitted repos (and generated from the form for novices). `upload` and `external_url` asset kinds work end-to-end: assets are hashed (sha256), cached in Blob Storage, copied into the per-app Azure Files share at deploy time, and mounted at the declared `mount_path` inside the deployed ACA app. `shared_reference` is schema-supported but rejected with a "coming soon" message (AC8.6). Missing-manifest submissions fall back to form-generated manifest with no assets.

**Architecture:** FCIS throughout. Pure: manifest schema validation (Pydantic), form→manifest mapping, sha256 streaming utility (strictly a function over a byte iterator — no file I/O), asset-kind dispatch table. Shell: Blob SAS mint, direct-to-blob uppy-driven uploads, server-side Blob→Azure-Files copy at deploy time, external URL fetch with streaming sha256 verification. The design said "Azure Files CSI or Blob mounts" — the 2026 investigation confirmed ACA supports Azure Files only; Blob is used as the staging/durable cache, Azure Files as the runtime mount.

**Tech Stack:** Pydantic v2 YAML validation; `PyYAML` for parsing; `azure-storage-blob` with SAS for upload (browser direct + `generate_blob_sas`); `azure-storage-file-share` for the per-app Files share populate step; `hashlib.sha256` streaming; `httpx` for external URL fetch; React `@uppy/core` + `@uppy/tus` or `@uppy/xhr-upload` against a SAS URL; `pydantic` errors for schema diagnostics.

**Scope:** Phase 8 of 8.

**Codebase verified:** 2026-04-23 — Phase 5 delivered Tier 3 provisioning with per-app Azure Files shares (`files.ensure_app_share`) and mount stubs (`/mnt/assets`). Phase 4 detection rules under `manifest/` exist for rule-level validation; Phase 8 adds schema-level validation + actual asset-fetch side effects. `asset` table exists from Phase 2 migration.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### rac-v1.AC8: Asset handling supports v1 kinds
- **rac-v1.AC8.1 Success:** An `upload` asset provided via the submission form is stored in Blob, its sha256 is computed server-side and persisted in `asset`, and it is mounted at the declared `mount_path` inside the deployed ACA app.
- **rac-v1.AC8.2 Success:** An `external_url` asset with a reachable URL and matching sha256 is fetched, verified, cached in Blob, and mounted.
- **rac-v1.AC8.3 Failure:** An `external_url` asset whose fetched content does not match its declared sha256 blocks deployment; the IT approver sees the mismatch in the approval UI with both expected and actual hashes.
- **rac-v1.AC8.4 Failure:** An `external_url` asset whose URL is unreachable puts the submission in `needs_user_action` with a clear explanation.
- **rac-v1.AC8.5 Edge:** A submission without a committed `rac.yaml` but with form-declared assets produces an equivalent parsed manifest internally, indistinguishable downstream from a researcher-committed manifest.
- **rac-v1.AC8.6 Edge:** A manifest that declares a `shared_reference` asset is rejected at submission time in v1 with a "shared references coming soon" message naming the specific entry.

**Verifies:** Functionality phase. Each task names which AC cases it tests.

---

## File Classification Policy

- `manifest/schema.py`: type-only (Pydantic models).
- `manifest/parser.py`: Functional Core (pure YAML→model + validation).
- `manifest/form_mapper.py`: Functional Core.
- `services/assets/sha256_stream.py`: Functional Core.
- `services/assets/upload.py`, `external_fetch.py`, `blob_to_files_copy.py`, `sas_minter.py`: Imperative Shell.
- `provisioning/aca.py` extension: Imperative Shell (already existed from Phase 5).
- React features: per Phase 2.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Manifest schema + parser (Functional Core)

**Verifies:** `rac-v1.AC8.5`, `rac-v1.AC8.6`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/manifest/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/manifest/schema.py` (type-only)
- Create: `apps/control-plane/backend/src/rac_control_plane/manifest/parser.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_manifest_parser.py`

**Implementation:**

`schema.py` — Pydantic v2 models:

```python
from pydantic import BaseModel, Field, HttpUrl, field_validator, ConfigDict
from typing import Literal, Annotated, Union

class UploadAsset(BaseModel):
    kind: Literal["upload"] = "upload"
    name: str                          # Logical name (e.g., "reference-genome")
    mount_path: str                    # Absolute path inside container (e.g., "/mnt/ref/genome.fa")
    sha256: str | None = None          # Computed server-side; None until upload completes
    size_bytes: int | None = None      # Computed server-side
    notes: str | None = None

class ExternalUrlAsset(BaseModel):
    kind: Literal["external_url"] = "external_url"
    name: str
    mount_path: str
    url: HttpUrl                       # Required
    sha256: str                        # Declared by researcher; verified at fetch
    size_bytes: int | None = None
    notes: str | None = None

class SharedReferenceAsset(BaseModel):
    kind: Literal["shared_reference"] = "shared_reference"
    name: str
    mount_path: str
    catalog_id: str                    # Reference into shared_reference_catalog — accepted in schema, rejected at submit time

Asset = Annotated[Union[UploadAsset, ExternalUrlAsset, SharedReferenceAsset], Field(discriminator="kind")]

class ManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    assets: list[Asset] = Field(default_factory=list)
    target_port: int = Field(default=8080, ge=1, le=65535)
    cpu_cores: float = Field(default=0.5, ge=0.25, le=2.0)
    memory_gb: float = Field(default=1.0, ge=0.5, le=8.0)
    env_vars: dict[str, str] = Field(default_factory=dict)

    @field_validator("assets")
    @classmethod
    def no_duplicate_names(cls, v):
        names = [a.name for a in v]
        if len(set(names)) != len(names):
            raise ValueError("asset names must be unique within a manifest")
        return v

    @field_validator("assets")
    @classmethod
    def no_duplicate_mount_paths(cls, v):
        paths = [a.mount_path for a in v]
        if len(set(paths)) != len(paths):
            raise ValueError("mount_path values must be unique within a manifest")
        return v
```

`parser.py` (pure):
- `def parse_manifest(yaml_text: str) -> ManifestV1`: `yaml.safe_load` → dict → `ManifestV1.model_validate`. On `yaml.YAMLError` → raise `ManifestParseError("yaml_syntax_error", f"Line {mark.line+1}: {problem}")`. On `pydantic.ValidationError` → raise `ManifestParseError` with a per-field list.
- `def reject_shared_references(manifest: ManifestV1) -> ManifestV1`: pure; iterates `assets`; if any has `kind=shared_reference`, raise `SharedReferenceNotYetSupportedError(entry_name=asset.name, message=f"shared references coming soon — asset '{asset.name}' is not yet supported in v1")`. Keep the schema-level acceptance (so future v2 tooling reading current manifests doesn't crash) but fail loudly at the submission boundary (AC8.6).
- `def manifest_from_dict(d: dict) -> ManifestV1`: for form-generated manifests; bypasses YAML parsing but runs the same Pydantic validation. Used by `form_mapper.py`.

Tests:
- Valid manifest with 0 assets parses.
- Valid manifest with 1 upload + 1 external_url parses.
- Manifest with duplicate asset names → `ManifestParseError`.
- Manifest with duplicate mount_paths → `ManifestParseError`.
- Manifest with `version: 2` → `ManifestParseError` (Pydantic rejects the Literal).
- Manifest with `shared_reference` passes parse but fails `reject_shared_references` with the entry name (AC8.6).
- Property test: any parsed-then-serialized-then-reparsed manifest equals the original.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_manifest_parser.py -v
```

**Commit:** `feat(control-plane): manifest schema + parser`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Form → manifest mapper (Functional Core)

**Verifies:** `rac-v1.AC8.5`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/manifest/form_mapper.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_form_mapper.py`

**Implementation:**

`form_mapper.py`:
- `@dataclass(frozen=True) class FormAssetInput`: `kind`, `name`, `mount_path`, optional `declared_url`, optional `declared_sha256`, optional `upload_blob_uri` (set after the client completes an uppy upload).
- `@dataclass(frozen=True) class FormSubmissionInput`: the submission-form payload shape.
- `def build_manifest_from_form(form: FormSubmissionInput) -> ManifestV1`: pure; constructs an equivalent `ManifestV1` and runs it through `parser.manifest_from_dict` for validation, so downstream consumers (provisioning) see the same validated shape whether the manifest came from `rac.yaml` or the form.

AC8.5 verification: tests assert that a form-generated manifest serialized to YAML and re-parsed equals the same manifest derived directly from a YAML file with the equivalent contents. Downstream code takes `ManifestV1`; it cannot distinguish source.

Tests: examples + round-trip property test.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_form_mapper.py -v
```

**Commit:** `feat(control-plane): form → manifest mapper`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Streaming sha256 (Functional Core)

**Verifies:** Foundation for AC8.1, AC8.2, AC8.3

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/assets/__init__.py`
- Create: `apps/control-plane/backend/src/rac_control_plane/services/assets/sha256_stream.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/test_sha256_stream.py`

**Implementation:**

`sha256_stream.py`:
- `def stream_sha256(chunks: Iterable[bytes]) -> tuple[str, int]`: consumes the iterable once, returns `(hex_digest, total_bytes)`. Pure function over its inputs — no I/O.
- `async def astream_sha256(chunks: AsyncIterator[bytes]) -> tuple[str, int]`: async variant for use with httpx streams. Still pure in the FCIS sense (it doesn't initiate I/O; it consumes a caller-provided iterator).

Tests:
- Known test vectors (empty, one byte, "abc").
- Random bytes: `stream_sha256([b"prefix", b"suffix"])` == `hashlib.sha256(b"prefixsuffix").hexdigest()`.
- Property test: splitting input into arbitrary chunks yields the same digest.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_sha256_stream.py -v
```

**Commit:** `feat(control-plane): streaming sha256 utility`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-6) -->

<!-- START_TASK_4 -->
### Task 4: Upload flow — SAS mint + direct Blob upload + server finalize

**Verifies:** `rac-v1.AC8.1`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/assets/sas_minter.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/services/assets/upload.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/src/rac_control_plane/api/routes/assets.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_upload_flow.py`

**Implementation:**

Upload architecture:
1. Client requests a SAS → browser uses `@uppy/xhr-upload` (or a native XHR) to PUT chunks directly to Blob Storage.
2. Client notifies the server when upload is complete with the client-computed sha256.
3. Server verifies the blob: reads it back (Azure Blob SDK, streaming) while recomputing sha256; compares to declared sha256 and client-reported sha256. On match → insert `asset` row linked to the submission with `blob_uri`, `sha256`, `size_bytes`, `status='ready'`. On mismatch → delete the blob, return 422 `sha256_mismatch`.

This pattern avoids routing researcher uploads through FastAPI workers (which would bottleneck on bandwidth) while still guaranteeing integrity (server re-hashes).

`sas_minter.py`:
- `async def mint_upload_sas(submission_id: UUID, asset_name: str, max_size_bytes: int, max_age_seconds: int = 3600) -> SasCredentials`:
  - Derives blob path: `f"submissions/{submission_id}/{asset_name}"` in container `researcher-uploads`.
  - Uses `generate_blob_sas` with user-delegation key (preferred) — managed identity of the Control Plane has `Storage Blob Data Contributor` on the container; calling `BlobServiceClient.get_user_delegation_key(...)` returns a key usable for SAS.
  - Permissions: `add=True, write=True, create=True`. No `read`. Short expiry.
  - Returns `{upload_url, blob_path, expires_at, max_size_bytes}`.

`upload.py`:
- `async def finalize_upload(session, *, submission_id: UUID, asset_name: str, blob_path: str, declared_sha256: str, declared_size_bytes: int | None, mount_path: str) -> Asset`:
  1. `BlobClient` for `blob_path` — stream download and call `astream_sha256`.
  2. If computed sha256 != declared sha256 → `BlobClient.delete_blob()`; raise `ValidationApiError("sha256_mismatch", ...)`.
  3. If `declared_size_bytes` set and computed size differs → same (size is cheap cross-check).
  4. INSERT `asset(id=uuidv7, submission_id, name, kind='upload', mount_path, blob_uri, sha256, size_bytes, status='ready', created_at)`.
  5. Return the inserted `asset` row.

`api/routes/assets.py`:
- `POST /submissions/{id}/assets/uploads/sas`: body `{name, mount_path, max_size_bytes}`. Submitter-only. Returns `SasCredentials`.
- `POST /submissions/{id}/assets/uploads/finalize`: body `{name, blob_path, declared_sha256, declared_size_bytes?, mount_path}`. Submitter-only. Calls `upload.finalize_upload`. Returns `Asset`.
- `GET /submissions/{id}/assets`: list. Submitter, approvers, admin.

Tests (integration with Azure Storage emulator — Azurite):
- End-to-end: mint SAS → client PUTs a fixture file → POST finalize → row exists with correct sha256 (AC8.1).
- sha256 mismatch → 422, blob deleted, no row (the server is the ground truth even if the client lied about its own computation).
- SAS expiry respected (test with short expiry).
- Permission: non-submitter on finalize → 403.

**Orphan blob cleanup:** If a researcher abandons a submission after minting a SAS but before calling `/finalize`, the uploaded blob sits in `researcher-uploads` with no corresponding `asset` row. Two mitigations:
1. **Lifecycle policy (Phase 1 `blob-storage.bicep`):** the `researcher-uploads` container already has a lifecycle policy set in Phase 1 Task 7 that deletes blobs with no `x-ms-blob-last-modified` update in 7 days. Ensure the lifecycle rule targets `researcher-uploads` specifically. No application code change needed.
2. **Operational note:** add a `docs/runbooks/orphan-blob-cleanup.md` runbook skeleton (one paragraph, links to the lifecycle policy rule name, `az storage blob list --prefix submissions/` for manual inspection). This is a documentation-only deliverable in this task.

Document the orphan cleanup lifecycle policy rule name in the `README.md` of `infra/modules/blob-storage.bicep`.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_upload_flow.py -v
```

**Commit:** `feat(control-plane): upload flow (SAS + finalize)`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: External URL fetch + sha256 verification

**Verifies:** `rac-v1.AC8.2`, `rac-v1.AC8.3`, `rac-v1.AC8.4`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/assets/external_fetch.py` (pattern: Imperative Shell)
- Create: `apps/control-plane/backend/tests/test_external_fetch.py`

**Implementation:**

`external_fetch.py`:
- `async def fetch_external_asset(session, *, submission_id: UUID, asset_name: str, url: HttpUrl, declared_sha256: str, mount_path: str) -> Asset`:
  1. `httpx.AsyncClient.stream("GET", url, timeout=settings.external_fetch_timeout_seconds, follow_redirects=True)`.
  2. Reject non-HTTPS URLs (configurable — default enforce HTTPS).
  3. Stream response body: while computing sha256, also upload to Blob (`BlobClient.upload_blob(data=generator, overwrite=True, length=content_length)`).
  4. On HTTP error (DNS, 4xx, 5xx, timeout): set submission `needs_user_action` via FSM, INSERT `approval_event(kind='external_asset_unreachable', detail={asset_name, url, error})`, raise `ExternalAssetError("unreachable", ...)` — AC8.4.
  5. After download: compare computed sha256 to declared. Mismatch → delete blob; insert `asset(..., status='hash_mismatch')` with expected vs actual; raise `HashMismatchError` — AC8.3. Submission stays in provisioning-blocked state until researcher updates the sha256 or URL.
  6. Match → insert `asset(..., status='ready')` — AC8.2.

Who triggers this? Two paths:
- Submission-time: when the researcher finalizes the submission with `external_url` assets, the Control Plane enqueues background fetches. Submission does not dispatch pipeline until all external assets are either `ready` or explicitly marked `skip` by a decision.
- Retry: admin UI button on the asset row re-runs the fetch.

Tests (with `respx` mocking httpx):
- Reachable URL, matching sha256 → AC8.2 (asset row `ready`).
- Reachable URL, content sha differs → AC8.3: row `hash_mismatch`, `expected_sha256`, `actual_sha256` both present; blob deleted.
- Unreachable URL (connection refused) → AC8.4: submission → `needs_user_action`, `approval_event` row.
- Large file (streaming path): handled without OOM (property-ish test with generated sizes up to some cap).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_external_fetch.py -v
```

**Commit:** `feat(control-plane): external URL asset fetch + verification`
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Blob → Azure Files copy at deploy time + ACA mount wiring

**Verifies:** `rac-v1.AC8.1`, `rac-v1.AC8.2`

**Files:**
- Create: `apps/control-plane/backend/src/rac_control_plane/services/assets/blob_to_files_copy.py` (pattern: Imperative Shell)
- Modify: `apps/control-plane/backend/src/rac_control_plane/provisioning/aca.py` (wire volume mounts into the `ContainerApp` model)
- Modify: `apps/control-plane/backend/src/rac_control_plane/services/provisioning/orchestrator.py` (invoke copy before creating the ACA app)
- Create: `apps/control-plane/backend/tests/test_blob_to_files_copy.py`

**Implementation:**

`blob_to_files_copy.py`:
- `async def populate_app_share_from_assets(session, *, app, submission) -> None`:
  1. Load all `ready`-status `asset` rows for `submission`.
  2. For each asset: `BlobClient` downloads streaming; `ShareFileClient` uploads to the per-app share at a path derived from `asset.name`. The target file in the share will be *mounted* into the container at `asset.mount_path` via volume mount.
  3. Set file metadata: `sha256`, `asset_id`, `source` (`upload` vs `external_url`).

Volume mount model in `provisioning/aca.py`: for each asset, add a `Volume(name=f"asset-{asset.id}", storage_type="AzureFile", storage_name=<share-name>)` (or a single volume with subdirectory mounts if ACA supports that — 2026 investigation said multiple AzureFile volumes per container ARE supported; use one volume per asset for clarity).

For each asset, add a `VolumeMount(volume_name=f"asset-{asset.id}", mount_path=<asset.mount_path>, sub_path=<path inside share>)` on the container.

Orchestrator: call `populate_app_share_from_assets` BEFORE `aca.create_or_update_app`.

Tests:
- Create fake assets + Azurite blob storage + Azurite file storage → run copy → files exist in the share with correct content and metadata.
- ContainerApp constructor called with expected `volumes` and `volume_mounts`.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_blob_to_files_copy.py apps/control-plane/backend/tests/test_provisioning_aca.py -v
```

**Commit:** `feat(control-plane): Blob→Files copy + ACA mount wiring`
<!-- END_TASK_6 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 7-9) -->

<!-- START_TASK_7 -->
### Task 7: Submission create path integrates manifest + assets

**Verifies:** `rac-v1.AC8.1`, `rac-v1.AC8.5`, `rac-v1.AC8.6`

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/services/submissions/create.py` (call manifest parser)
- Modify: `apps/control-plane/backend/src/rac_control_plane/services/submissions/finalize.py` (NEW — pattern: Imperative Shell — blocks pipeline dispatch until assets resolve)
- Modify: `apps/control-plane/backend/src/rac_control_plane/api/routes/submissions.py` (pipeline dispatch is now triggered from finalize, not create)
- Create: `apps/control-plane/backend/tests/test_submission_manifest_integration.py`

**Implementation:**

Submission create flow updates:
1. If `manifest` dict provided in request body → call `parser.manifest_from_dict` + `reject_shared_references`.
2. Else if the cloned repo has `rac.yaml` (Phase 4's `RepoContext` builder reads this) → `parser.parse_manifest` + `reject_shared_references`.
3. Else → call `form_mapper.build_manifest_from_form(form_input)` where form_input is the rest of the submission payload (assets declared through the form UI).
4. Persist resolved `ManifestV1` as `submission.manifest` (JSONB).
5. For each `upload` asset: expect the client has already completed the upload path (SAS + finalize). Look up `asset` row by `(submission_id, name)`; if missing → submission → `needs_user_action` with note "upload pending for asset <name>".
6. For each `external_url` asset: enqueue `external_fetch.fetch_external_asset` as a background task. Pipeline dispatch waits until all assets are `ready` or `hash_mismatch`/`unreachable`.
7. If `shared_reference` asset → AC8.6 "coming soon" error returned immediately.

New module `submissions/finalize.py`: **signal-triggered** (NOT polled). See README.md cross-phase decision "Finalize trigger." The `finalize_submission` function is called directly from two callers:
1. `assets/upload.finalize_upload` — after an upload asset is finalized, it checks whether all sibling assets are now `ready` and, if so, calls `finalize_submission`.
2. `assets/external_fetch.fetch_external_asset` — same post-fetch check.

There is no background poller. If an asset completes but `finalize_submission` crashes (e.g., DB error), the submission stays in `awaiting_scan`. The operator can manually trigger finalization via an admin endpoint `POST /admin/submissions/{id}/force-finalize` (add to admin routes). This avoids polling-loop complexity entirely and is consistent with the ACA event-driven model.

`finalize_submission(session, submission_id)` checks whether all assets are `ready`; if so, dispatches the pipeline. If any are `hash_mismatch` or `unreachable`, transitions to `needs_user_action` with actionable findings surfaced in UI.

Tests:
- Submission with no manifest, no assets → manifest auto-generated with empty assets; pipeline dispatched (AC8.5).
- Submission with form-declared `upload` asset; after upload finalizes → `asset.status='ready'`; pipeline dispatched.
- Submission with `shared_reference` asset → 422 with entry name in message (AC8.6).
- Submission with `external_url` asset where sha256 does NOT match → pipeline not dispatched; submission in `needs_user_action` with `approval_event` listing both hashes (AC8.3).
- Submission with unreachable URL → `needs_user_action` (AC8.4).

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/test_submission_manifest_integration.py -v
```

**Commit:** `feat(control-plane): submission flow integrates manifest + assets`
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Asset rules extensions (undeclared_assets, missing_sha)

**Verifies:** Complements AC4.* and supports AC8.3, AC8.4

**Files:**
- Modify: `apps/control-plane/backend/src/rac_control_plane/detection/rules/manifest/undeclared_assets.py` (update to use parsed manifest from ManifestV1)
- Create: `apps/control-plane/backend/src/rac_control_plane/detection/rules/manifest/missing_sha.py` (pattern: Functional Core)
- Create: `apps/control-plane/backend/tests/detection/test_missing_sha.py`

**Implementation:**

`missing_sha.py`: pure rule that emits a finding for every `ExternalUrlAsset` whose `sha256` is missing or an obviously-invalid string (not 64 hex chars). `severity="error"` (blocks submission progression because an unverified external URL is a supply-chain risk).

Update `undeclared_assets.py` to consume `ctx.manifest` as a dict (matching Phase 4's `RepoContext` shape) and cross-check Dockerfile `COPY` destinations against declared asset mount paths. Fires when the Dockerfile copies data into a path that the manifest also declares as an asset mount (collision → ambiguous which wins at runtime).

Tests: property-based + examples.

**Verification:**
```bash
uv run --project apps/control-plane/backend pytest apps/control-plane/backend/tests/detection/test_missing_sha.py apps/control-plane/backend/tests/detection/test_undeclared_assets.py -v
```

**Commit:** `feat(detection): manifest missing_sha rule + undeclared_assets refresh`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: React features — manifest form, asset tiles, IT approval hash view

**Verifies:** `rac-v1.AC8.3` (IT view), AC8.5 (UI)

**Files:**
- Create: `apps/control-plane/frontend/src/features/manifest-form/index.tsx`
- Create: `apps/control-plane/frontend/src/features/manifest-form/asset-tile-upload.tsx`
- Create: `apps/control-plane/frontend/src/features/manifest-form/asset-tile-external-url.tsx`
- Create: `apps/control-plane/frontend/src/features/manifest-form/asset-tile-shared-reference.tsx` (disabled; "Coming in v2" copy)
- Create: `apps/control-plane/frontend/src/features/approval-queue/asset-hash-mismatch-card.tsx`
- Create: `apps/control-plane/frontend/src/tests/manifest-form.test.tsx`

**Implementation:**

Manifest form (used at submission time):
- Three-tile layout: Upload | External URL | Shared reference (grayed out with tooltip "Coming in v2 — see docs for preview").
- Upload tile: embeds Uppy (`@uppy/core`, `@uppy/dashboard`, `@uppy/xhr-upload`) pointed at `POST /submissions/{id}/assets/uploads/sas` for SAS mint and then uploading directly to Blob. Shows progress bar per file, computes sha256 client-side (for UX/cross-check; server re-computes authoritatively), calls `/finalize` endpoint after upload completes.
- External URL tile: form fields `name`, `url`, `declared_sha256`, `mount_path`. Optional "Fetch sha256 now" button that calls a helper endpoint to HEAD + stream-download + compute — researcher can copy the server-computed hash back into the field before submit.

IT approver hash-mismatch card: for any asset row with `status='hash_mismatch'`, displays expected vs actual hashes side-by-side (AC8.3), with a "Retry fetch" button (admin only) and a link to the original declared URL.

Tests: Uppy mounted, mock SAS endpoint returns a URL, simulate chunk upload, assert finalize endpoint called with client-computed sha256; mismatch view renders with both hashes.

**Verification:**
```bash
cd /home/sysop/rac/apps/control-plane/frontend && pnpm test -- manifest-form
```

**Commit:** `feat(control-plane): manifest form + asset tiles + hash-mismatch view`
<!-- END_TASK_9 -->

<!-- END_SUBCOMPONENT_C -->

<!-- START_TASK_10 -->
### Task 10: End-to-end acceptance — asset handling

**Verifies:** all Phase 8 ACs (meta)

**Files:** None

**Implementation:**

Against dev:

1. Submit a repo with form-declared `upload` asset (small file, 1 MB, a synthetic dataset); upload completes; `asset` row `ready`; final deploy → ACA app mounts the file at the declared path; `az containerapp exec` + `cat /mnt/data/file` returns the same bytes (AC8.1).
2. Submit with `external_url` pointing at a reachable URL with correct sha256 → `asset.status='ready'`; deploy mounts it (AC8.2).
3. Submit with `external_url` with a deliberate sha256 mismatch → submission blocked; IT approver UI shows both hashes (AC8.3).
4. Submit with `external_url` pointing at 127.0.0.1:1 (unreachable) → submission `needs_user_action` with clear message (AC8.4).
5. Submit with a `rac.yaml` in the repo that's identical to what the form would produce → both paths yield identical downstream behavior; `submission.manifest` JSONB equivalent (AC8.5).
6. Submit with a `shared_reference` entry in `rac.yaml` → 422 at submit time with "shared references coming soon — asset '<name>' is not yet supported in v1" (AC8.6).

Findings → `phase8-acceptance-report.md`.

**Verification:** commands above.

**Commit:** None.
<!-- END_TASK_10 -->

---

## Phase 8 Done Checklist

- [ ] Manifest schema + parser with property tests
- [ ] Form→manifest mapper produces equivalent output (AC8.5)
- [ ] Shared reference rejected at submission time with entry name (AC8.6)
- [ ] SAS mint + finalize flow verifies server-side sha256 (AC8.1)
- [ ] Orphan blob cleanup: lifecycle policy targets `researcher-uploads`; `docs/runbooks/orphan-blob-cleanup.md` exists (Task 4)
- [ ] External URL fetch streams + verifies sha256 (AC8.2, AC8.3)
- [ ] Unreachable URL transitions submission to `needs_user_action` (AC8.4)
- [ ] `submissions/finalize.py` is signal-triggered (not polled); callers are `finalize_upload` and `fetch_external_asset` (Task 7)
- [ ] Blob→Files copy populates the per-app Azure Files share at deploy time
- [ ] ACA app includes the correct volumes + volume_mounts for each declared asset
- [ ] missing_sha detection rule blocks submissions with unverified external URLs
- [ ] Manifest form + asset tiles + hash-mismatch view pass vitest
- [ ] End-to-end acceptance on dev covers every AC8.* case
- [ ] FCIS classification on every non-exempt file
