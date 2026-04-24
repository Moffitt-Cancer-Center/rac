"""Tests for dockerfile/missing_user rule."""

from pathlib import Path
from uuid import uuid4

from rac_control_plane.detection.contracts import RepoContext, RepoFile
from rac_control_plane.detection.rules.dockerfile.missing_user import RULE, _evaluate


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


def test_no_user_instruction_fires(tmp_path: Path) -> None:
    """Dockerfile without any USER → 1 finding."""
    df = "FROM python:3.12\nRUN pip install flask\nCMD [\"python\", \"app.py\"]\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "dockerfile/missing_user"
    assert f.severity == "warn"
    assert "USER" in f.detail
    assert f.file_path == "Dockerfile"


def test_user_instruction_present_no_finding(tmp_path: Path) -> None:
    """Dockerfile with USER → 0 findings."""
    df = "FROM python:3.12\nRUN pip install flask\nUSER appuser\nCMD [\"python\", \"app.py\"]\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 0


def test_only_fires_once_for_entire_dockerfile(tmp_path: Path) -> None:
    """Even with many stages, missing USER fires exactly once (single-stage)."""
    df = "FROM python:3.12\nRUN echo hello\nRUN echo world\n"
    findings = list(_evaluate(_ctx(df, tmp_path)))
    assert len(findings) == 1


def test_rule_constant() -> None:
    assert RULE.rule_id == "dockerfile/missing_user"
    assert RULE.version == 1
