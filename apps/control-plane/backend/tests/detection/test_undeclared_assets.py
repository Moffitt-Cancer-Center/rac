"""Tests for manifest/undeclared_assets rule."""

from pathlib import Path
from uuid import uuid4

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.manifest.undeclared_assets import RULE, _evaluate


def _ctx(dockerfile_text: str, manifest: dict | None, tmp_path: Path) -> RepoContext:
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text=dockerfile_text,
        files=(),
        manifest=manifest,
        submission_metadata={},
    )


def test_copy_collides_with_asset_mount_fires(tmp_path: Path) -> None:
    """COPY to declared asset mount_path → 1 finding."""
    df = "FROM python:3.12\nCOPY data /app/data\nCMD [\"python\", \"app.py\"]\n"
    manifest = {"assets": [{"name": "dataset", "mount_path": "/app/data"}]}
    findings = list(_evaluate(_ctx(df, manifest, tmp_path)))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "manifest/undeclared_assets"
    assert "/app/data" in f.detail


def test_copy_to_different_path_no_finding(tmp_path: Path) -> None:
    """COPY to a path not in manifest → 0 findings."""
    df = "FROM python:3.12\nCOPY app.py /app/app.py\n"
    manifest = {"assets": [{"name": "dataset", "mount_path": "/app/data"}]}
    findings = list(_evaluate(_ctx(df, manifest, tmp_path)))
    assert len(findings) == 0


def test_no_manifest_no_finding(tmp_path: Path) -> None:
    """No manifest → 0 findings."""
    df = "FROM python:3.12\nCOPY data /app/data\n"
    findings = list(_evaluate(_ctx(df, None, tmp_path)))
    assert len(findings) == 0


def test_manifest_no_assets_no_finding(tmp_path: Path) -> None:
    """Manifest without assets key → 0 findings."""
    df = "FROM python:3.12\nCOPY data /app/data\n"
    manifest: dict = {"name": "my-app"}
    findings = list(_evaluate(_ctx(df, manifest, tmp_path)))
    assert len(findings) == 0


def test_ac46_two_copy_collisions(tmp_path: Path) -> None:
    """Two COPY instructions each colliding with an asset → 2 findings (AC4.6)."""
    df = (
        "FROM python:3.12\n"
        "COPY models /app/models\n"
        "COPY data /app/data\n"
    )
    manifest = {
        "assets": [
            {"name": "models", "mount_path": "/app/models"},
            {"name": "data", "mount_path": "/app/data"},
        ]
    }
    findings = list(_evaluate(_ctx(df, manifest, tmp_path)))
    assert len(findings) == 2


def test_rule_constant() -> None:
    assert RULE.rule_id == "manifest/undeclared_assets"
    assert RULE.version == 1
