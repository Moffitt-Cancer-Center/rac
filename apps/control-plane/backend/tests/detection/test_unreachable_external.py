"""Tests for manifest/unreachable_external rule.

This rule only checks STRUCTURAL issues (missing sha256, unparseable URL),
not actual network reachability. Reachability is Phase 8.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.manifest.unreachable_external import RULE, _evaluate


def _ctx(manifest: dict | None, tmp_path: Path) -> RepoContext:
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\n",
        files=(),
        manifest=manifest,
        submission_metadata={},
    )


def test_missing_sha256_fires(tmp_path: Path) -> None:
    """External asset without sha256 → 1 finding."""
    manifest = {
        "assets": [
            {"name": "model", "url": "https://example.com/model.pkl"}
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "manifest/unreachable_external"
    assert "sha256" in f.detail.lower()


def test_sha256_present_no_finding(tmp_path: Path) -> None:
    """External asset with sha256 → 0 findings."""
    manifest = {
        "assets": [
            {
                "name": "model",
                "url": "https://example.com/model.pkl",
                "sha256": "abc123def456" * 4,
            }
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 0


def test_checksum_field_accepted(tmp_path: Path) -> None:
    """'checksum' field also satisfies the integrity requirement."""
    manifest = {
        "assets": [
            {
                "name": "model",
                "url": "https://example.com/model.pkl",
                "checksum": "abc123",
            }
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 0


def test_unparseable_url_fires(tmp_path: Path) -> None:
    """Unparseable URL → finding about invalid URL (not sha256)."""
    manifest = {
        "assets": [
            {"name": "bad", "url": "https://"}  # missing netloc
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 1
    assert "parseable" in findings[0].detail.lower() or "parsed" in findings[0].detail.lower() or "parse" in findings[0].detail.lower() or "Unparseable" in findings[0].detail


def test_local_path_not_flagged(tmp_path: Path) -> None:
    """Assets with local paths (no http://) are not flagged."""
    manifest = {
        "assets": [
            {"name": "local_data", "url": "/mnt/data/file.csv"}
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 0


def test_no_manifest_no_finding(tmp_path: Path) -> None:
    findings = list(_evaluate(_ctx(None, tmp_path)))
    assert len(findings) == 0


def test_ac46_two_assets_missing_sha256(tmp_path: Path) -> None:
    """Two assets missing sha256 → two findings (AC4.6)."""
    manifest = {
        "assets": [
            {"name": "model1", "url": "https://example.com/model1.pkl"},
            {"name": "model2", "url": "https://example.com/model2.pkl"},
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 2


# ---------------------------------------------------------------------------
# Property test: assets with valid URL and sha256 → 0 findings
# ---------------------------------------------------------------------------

@given(
    sha256=st.from_regex(r"[0-9a-f]{64}", fullmatch=True),
    path=st.from_regex(r"/[a-z]{3,10}/[a-z]{3,10}\.pkl", fullmatch=True),
)
@hyp_settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_valid_asset_no_finding(sha256: str, path: str, tmp_path: Path) -> None:
    """Property: assets with valid https URL and sha256 → 0 findings."""
    manifest = {
        "assets": [
            {
                "name": "asset",
                "url": f"https://files.example.com{path}",
                "sha256": sha256,
            }
        ]
    }
    findings = list(_evaluate(_ctx(manifest, tmp_path)))
    assert len(findings) == 0


def test_rule_constant() -> None:
    assert RULE.rule_id == "manifest/unreachable_external"
    assert RULE.version == 1
    # Verify the docstring explains the FCIS purity split
    assert "structural" in RULE.evaluate.__doc__ or "structural" in (
        RULE.evaluate.__doc__ or ""
    ) or "Phase 8" in _evaluate.__doc__ or "structural" in (_evaluate.__doc__ or "")
