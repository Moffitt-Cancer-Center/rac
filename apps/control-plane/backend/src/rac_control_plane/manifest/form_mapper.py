# pattern: Functional Core
"""rac_control_plane.manifest.form_mapper — convert form submission data to ManifestV1.

Pure: no I/O. Raises ManifestParseError on invalid inputs so downstream
consumers (provisioning, detection) see the same validated shape whether the
manifest originated from rac.yaml or the submission form (AC8.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from rac_control_plane.manifest.parser import ManifestParseError, manifest_from_dict
from rac_control_plane.manifest.schema import ManifestV1


@dataclass(frozen=True)
class FormAssetInput:
    """A single asset as declared via the submission form UI."""

    kind: Literal["upload", "external_url", "shared_reference"]
    name: str
    mount_path: str
    declared_url: str | None = None         # external_url: the URL to fetch
    declared_sha256: str | None = None      # external_url: researcher-declared sha256
    upload_blob_uri: str | None = None      # upload: set after client completes upload
    catalog_id: str | None = None           # shared_reference: catalog entry id
    notes: str | None = None


@dataclass(frozen=True)
class FormSubmissionInput:
    """The full form payload for a submission."""

    assets: list[FormAssetInput] = field(default_factory=list)
    target_port: int = 8080
    cpu_cores: float = 0.5
    memory_gb: float = 1.0
    env_vars: dict[str, str] | None = None


def _build_asset_dict(fa: FormAssetInput) -> dict[str, Any]:
    """Convert a FormAssetInput into the dict shape expected by ManifestV1."""
    if fa.kind == "upload":
        return {
            "kind": "upload",
            "name": fa.name,
            "mount_path": fa.mount_path,
            "notes": fa.notes,
        }

    if fa.kind == "external_url":
        if not fa.declared_url or not fa.declared_sha256:
            raise ManifestParseError(
                code="external_url_missing_fields",
                message=(
                    f"external_url asset '{fa.name}' requires both "
                    "'declared_url' and 'declared_sha256'"
                ),
            )
        return {
            "kind": "external_url",
            "name": fa.name,
            "mount_path": fa.mount_path,
            "url": fa.declared_url,
            "sha256": fa.declared_sha256,
            "notes": fa.notes,
        }

    if fa.kind == "shared_reference":
        return {
            "kind": "shared_reference",
            "name": fa.name,
            "mount_path": fa.mount_path,
            "catalog_id": fa.catalog_id or "",
        }

    raise ManifestParseError(  # pragma: no cover
        code="unknown_asset_kind",
        message=f"unknown asset kind: {fa.kind!r}",
    )


def build_manifest_from_form(form: FormSubmissionInput) -> ManifestV1:
    """Convert a FormSubmissionInput into a validated ManifestV1.

    Runs the same Pydantic validation as parse_manifest so that downstream
    consumers cannot distinguish source (AC8.5).

    Raises ManifestParseError on invalid inputs.
    """
    asset_dicts = [_build_asset_dict(fa) for fa in form.assets]

    manifest_dict: dict[str, Any] = {
        "version": 1,
        "assets": asset_dicts,
        "target_port": form.target_port,
        "cpu_cores": form.cpu_cores,
        "memory_gb": form.memory_gb,
        "env_vars": form.env_vars or {},
    }

    return manifest_from_dict(manifest_dict)
