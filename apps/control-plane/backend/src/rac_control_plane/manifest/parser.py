# pattern: Functional Core
"""rac_control_plane.manifest.parser — pure YAML → ManifestV1 parsing.

All functions are pure: they take plain values and return ManifestV1 or raise
ManifestParseError. No I/O, no side effects.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from rac_control_plane.manifest.schema import ManifestV1, SharedReferenceAsset


class ManifestParseError(Exception):
    """Raised when a manifest cannot be parsed or fails validation."""

    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: list[dict[str, Any]] = details or []


class SharedReferenceNotYetSupportedError(ManifestParseError):
    """Raised when a manifest contains a shared_reference asset (AC8.6)."""

    def __init__(self, entry_name: str) -> None:
        super().__init__(
            code="shared_reference_not_supported",
            message=(
                f"shared references coming soon — asset '{entry_name}' "
                "is not yet supported in v1"
            ),
        )
        self.entry_name = entry_name


def parse_manifest(yaml_text: str) -> ManifestV1:
    """Parse rac.yaml text into a validated ManifestV1.

    Raises ManifestParseError on YAML syntax errors or Pydantic validation
    failures. Does not raise on shared_reference assets — call
    reject_shared_references() separately at the submission boundary.
    """
    try:
        raw: Any = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            loc = f"Line {mark.line + 1}, column {mark.column + 1}"
        else:
            loc = "unknown location"
        problem = getattr(exc, "problem", str(exc))
        raise ManifestParseError(
            code="yaml_syntax_error",
            message=f"{loc}: {problem}",
        ) from exc

    if not isinstance(raw, dict):
        raise ManifestParseError(
            code="yaml_syntax_error",
            message="rac.yaml must be a YAML mapping at the top level",
        )

    return manifest_from_dict(raw)


def manifest_from_dict(d: dict[str, Any]) -> ManifestV1:
    """Validate an already-parsed dict and return a ManifestV1.

    Used by form_mapper so that form-generated manifests pass through the same
    Pydantic validation as YAML-parsed ones (AC8.5).

    Raises ManifestParseError on validation failures.
    """
    try:
        return ManifestV1.model_validate(d)
    except ValidationError as exc:
        details = [
            {
                "loc": list(err["loc"]),
                "msg": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        raise ManifestParseError(
            code="validation_error",
            message="manifest failed schema validation",
            details=details,
        ) from exc


def reject_shared_references(manifest: ManifestV1) -> ManifestV1:
    """Raise SharedReferenceNotYetSupportedError on the first shared_reference
    asset found; otherwise return the manifest unchanged (AC8.6).

    This is the submission-boundary guard. The schema accepts shared_reference
    so future v2 tooling can read current manifests without crashing; this
    function enforces the v1 runtime constraint.
    """
    for asset in manifest.assets:
        if isinstance(asset, SharedReferenceAsset):
            raise SharedReferenceNotYetSupportedError(entry_name=asset.name)
    return manifest
