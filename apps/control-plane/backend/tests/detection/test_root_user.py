"""Tests for dockerfile/root_user rule."""

from pathlib import Path
from uuid import uuid4

from rac_control_plane.detection.contracts import RepoContext
from rac_control_plane.detection.rules.dockerfile.root_user import RULE, _evaluate


def _ctx(dockerfile_text: str, tmp_path: Path) -> RepoContext:
    return RepoContext(
        repo_root=tmp_path,
        submission_id=uuid4(),
        dockerfile_path="Dockerfile",
        dockerfile_text=dockerfile_text,
        files=(),
        manifest=None,
        submission_metadata={},
    )


def test_user_root_fires(tmp_path: Path) -> None:
    """USER root → 1 finding."""
    df = "FROM ubuntu:22.04\nUSER root\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "dockerfile/root_user"
    assert f.severity == "warn"
    assert "root" in f.detail
    assert f.line_ranges == ((2, 2),)


def test_user_zero_fires(tmp_path: Path) -> None:
    """USER 0 (numeric root) → 1 finding."""
    df = "FROM ubuntu:22.04\nUSER 0\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 1


def test_user_nonroot_no_finding(tmp_path: Path) -> None:
    """USER appuser → 0 findings."""
    df = "FROM ubuntu:22.04\nUSER appuser\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 0


def test_user_numeric_nonroot_no_finding(tmp_path: Path) -> None:
    """USER 1001 → 0 findings."""
    df = "FROM ubuntu:22.04\nUSER 1001\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 0


def test_no_user_instruction_no_finding(tmp_path: Path) -> None:
    """No USER at all → root_user does NOT fire (missing_user handles it)."""
    df = "FROM ubuntu:22.04\nRUN echo hello\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 0


def test_nonroot_then_root_fires(tmp_path: Path) -> None:
    """USER appuser followed by USER root → fires because last USER is root."""
    df = "FROM ubuntu:22.04\nUSER appuser\nUSER root\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 1
    assert findings[0].line_ranges == ((3, 3),)


def test_root_then_nonroot_no_finding(tmp_path: Path) -> None:
    """USER root followed by USER appuser → no finding (last USER is non-root)."""
    df = "FROM ubuntu:22.04\nUSER root\nUSER appuser\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 0


def test_rule_constant() -> None:
    assert RULE.rule_id == "dockerfile/root_user"
    assert RULE.version == 1
