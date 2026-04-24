"""Tests for manifest/missing_sha detection rule.

Verifies:
- External asset with valid sha256 → 0 findings.
- External asset with missing sha256 → 1 finding, severity=error.
- External asset with 63-char sha → 1 finding.
- External asset with non-hex chars → 1 finding.
- Upload asset (no sha declared at manifest time) → 0 findings.
- shared_reference asset → 0 findings.
- 2 external assets, one with missing sha → 1 finding for the bad one only.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.manifest.missing_sha import (
    RULE,
    RULE_ID,
    RULE_VERSION,
    _evaluate,
    _is_valid_sha256,
)

VALID_SHA = "a" * 64  # 64 hex chars — valid


def _ctx(manifest: dict | None, tmp_path: Path) -> RepoContext:
    """Helper: build a minimal RepoContext with the given manifest."""
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=(),
        manifest=manifest,
        submission_metadata={},
    )


# ---------------------------------------------------------------------------
# _is_valid_sha256 unit tests
# ---------------------------------------------------------------------------


def test_valid_sha256_lowercase() -> None:
    assert _is_valid_sha256("a" * 64) is True


def test_valid_sha256_uppercase() -> None:
    assert _is_valid_sha256("A" * 64) is True


def test_valid_sha256_mixed_case() -> None:
    sha = "abcdef1234567890" * 4  # 64 chars
    assert _is_valid_sha256(sha) is True


def test_invalid_sha256_too_short() -> None:
    assert _is_valid_sha256("a" * 63) is False


def test_invalid_sha256_too_long() -> None:
    assert _is_valid_sha256("a" * 65) is False


def test_invalid_sha256_non_hex() -> None:
    assert _is_valid_sha256("g" * 64) is False


def test_invalid_sha256_empty() -> None:
    assert _is_valid_sha256("") is False


# ---------------------------------------------------------------------------
# _evaluate rule tests
# ---------------------------------------------------------------------------


def test_external_asset_with_valid_sha_no_finding(tmp_path: Path) -> None:
    """External asset with valid 64-char hex sha256 → 0 findings."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "dataset",
                "sha256": VALID_SHA,
                "mount_path": "/mnt/data",
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert findings == []


def test_external_asset_missing_sha_one_finding(tmp_path: Path) -> None:
    """External asset with no sha256 key → 1 finding with severity='error'."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "my-dataset",
                "mount_path": "/mnt/data",
                # no sha256 key
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == RULE_ID
    assert f.rule_version == RULE_VERSION
    assert f.severity == "error"
    assert "my-dataset" in f.title
    assert f.file_path == "rac.yaml"
    assert f.suggested_action == "override"


def test_external_asset_explicit_none_sha_one_finding(tmp_path: Path) -> None:
    """External asset with sha256=None → 1 finding."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "my-dataset",
                "sha256": None,
                "mount_path": "/mnt/data",
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert len(findings) == 1


def test_external_asset_63_char_sha_one_finding(tmp_path: Path) -> None:
    """External asset with 63-char sha256 → 1 finding (too short)."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "bad-length",
                "sha256": "a" * 63,
                "mount_path": "/mnt/data",
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert len(findings) == 1
    assert "bad-length" in findings[0].title


def test_external_asset_non_hex_chars_one_finding(tmp_path: Path) -> None:
    """External asset with non-hex characters in sha256 → 1 finding."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "non-hex",
                "sha256": "z" * 64,  # 'z' is not hex
                "mount_path": "/mnt/data",
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert len(findings) == 1
    assert "non-hex" in findings[0].title


def test_upload_asset_no_finding(tmp_path: Path) -> None:
    """Upload asset has no sha at manifest time → 0 findings (sha is set after upload)."""
    manifest = {
        "assets": [
            {
                "kind": "upload",
                "name": "my-upload",
                "mount_path": "/mnt/data",
                # sha256 is intentionally absent for upload assets
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert findings == []


def test_shared_reference_asset_no_finding(tmp_path: Path) -> None:
    """shared_reference asset → 0 findings (handled by other rules)."""
    manifest = {
        "assets": [
            {
                "kind": "shared_reference",
                "name": "hg38",
                "catalog_id": "hg38-v1",
                "mount_path": "/mnt/ref",
            }
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert findings == []


def test_two_external_assets_one_missing_one_finding(tmp_path: Path) -> None:
    """2 external assets, one with missing sha → 1 finding for the bad one only."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "good-asset",
                "sha256": VALID_SHA,
                "mount_path": "/mnt/good",
            },
            {
                "kind": "external_url",
                "name": "bad-asset",
                "mount_path": "/mnt/bad",
                # missing sha256
            },
        ]
    }
    findings = _evaluate(_ctx(manifest, tmp_path))
    assert len(findings) == 1
    assert "bad-asset" in findings[0].title
    assert "good-asset" not in findings[0].title


def test_no_manifest_no_findings(tmp_path: Path) -> None:
    """No manifest → 0 findings."""
    findings = _evaluate(_ctx(None, tmp_path))
    assert findings == []


def test_manifest_no_assets_no_findings(tmp_path: Path) -> None:
    """Manifest with no assets key → 0 findings."""
    findings = _evaluate(_ctx({}, tmp_path))
    assert findings == []


def test_rule_constant() -> None:
    """RULE object has correct metadata."""
    assert RULE.rule_id == RULE_ID
    assert RULE.version == RULE_VERSION
    assert RULE.default_severity == "error"
    assert callable(RULE.evaluate)


def test_rule_evaluate_callable(tmp_path: Path) -> None:
    """RULE.evaluate can be called directly."""
    manifest = {
        "assets": [
            {
                "kind": "external_url",
                "name": "missing-sha",
                "mount_path": "/mnt/data",
            }
        ]
    }
    findings = list(RULE.evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 1
