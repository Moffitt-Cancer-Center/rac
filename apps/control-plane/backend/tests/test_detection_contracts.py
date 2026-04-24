"""Tests for detection contracts: Rule, Finding, RepoContext.

Verifies:
- Correct field construction
- Frozen dataclass immutability (FrozenInstanceError on field assignment)
- Finding.rule_id whitespace validator
- AutoFixAction and Rule equality + hashability
- RepoContext.read() path traversal guard
"""

import dataclasses
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from rac_control_plane.detection.contracts import (
    AutoFixAction,
    Finding,
    RepoContext,
    RepoFile,
    Rule,
)


# ---------------------------------------------------------------------------
# RepoFile
# ---------------------------------------------------------------------------

def test_repo_file_construction() -> None:
    f = RepoFile(path="src/app.py", size_bytes=1024)
    assert f.path == "src/app.py"
    assert f.size_bytes == 1024


def test_repo_file_frozen() -> None:
    f = RepoFile(path="src/app.py", size_bytes=1024)
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.path = "other.py"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RepoContext
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> RepoContext:
    """Build a minimal RepoContext for testing."""
    submission_id = uuid4()
    return RepoContext(
        repo_root=tmp_path,
        submission_id=submission_id,
        dockerfile_path="Dockerfile",
        dockerfile_text="FROM python:3.12\nRUN echo hello\n",
        files=(RepoFile(path="Dockerfile", size_bytes=30),),
        manifest=None,
        submission_metadata={"pi_principal_id": str(uuid4()), "paper_title": "Test"},
    )


def test_repo_context_construction(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    assert ctx.repo_root == tmp_path
    assert isinstance(ctx.submission_id, UUID)
    assert ctx.dockerfile_path == "Dockerfile"
    assert ctx.manifest is None


def test_repo_context_frozen(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.dockerfile_path = "other/Dockerfile"  # type: ignore[misc]


def test_repo_context_read_valid_file(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_bytes(b"print('hello')")
    ctx = _make_context(tmp_path)
    content = ctx.read("app.py")
    assert content == b"print('hello')"


def test_repo_context_read_traversal_double_dot(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    with pytest.raises(ValueError, match="traversal"):
        ctx.read("../../etc/passwd")


def test_repo_context_read_absolute_path(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    with pytest.raises(ValueError, match="Absolute"):
        ctx.read("/etc/passwd")


def test_repo_context_read_missing_file(tmp_path: Path) -> None:
    ctx = _make_context(tmp_path)
    with pytest.raises(FileNotFoundError):
        ctx.read("nonexistent.py")


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

def test_finding_construction() -> None:
    f = Finding(
        rule_id="dockerfile/inline_downloads",
        rule_version=1,
        severity="warn",
        title="Inline download in Dockerfile",
        detail="wget found",
        line_ranges=((5, 5),),
        file_path="Dockerfile",
        suggested_action="override",
    )
    assert f.rule_id == "dockerfile/inline_downloads"
    assert f.rule_version == 1
    assert f.severity == "warn"
    assert f.line_ranges == ((5, 5),)
    assert f.suggested_action == "override"
    assert f.auto_fix is None


def test_finding_frozen() -> None:
    f = Finding(
        rule_id="test/rule",
        rule_version=1,
        severity="info",
        title="Test",
        detail="Test detail",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.severity = "error"  # type: ignore[misc]


def test_finding_rule_id_with_slash_allowed() -> None:
    """rule_id may contain '/' (category/name pattern) but no whitespace."""
    f = Finding(
        rule_id="repo/secrets_in_repo",
        rule_version=1,
        severity="warn",
        title="Secret found",
        detail="AWS key detected",
    )
    assert "/" in f.rule_id


def test_finding_rule_id_whitespace_raises() -> None:
    """rule_id must not contain whitespace — __post_init__ validates this."""
    with pytest.raises(ValueError, match="whitespace"):
        Finding(
            rule_id="bad rule id",
            rule_version=1,
            severity="warn",
            title="Test",
            detail="Detail",
        )


def test_finding_rule_id_tab_raises() -> None:
    """Tab character also counts as whitespace."""
    with pytest.raises(ValueError, match="whitespace"):
        Finding(
            rule_id="bad\trule",
            rule_version=1,
            severity="warn",
            title="Test",
            detail="Detail",
        )


# ---------------------------------------------------------------------------
# AutoFixAction
# ---------------------------------------------------------------------------

def test_auto_fix_action_construction() -> None:
    fix = AutoFixAction(
        kind="replace_line",
        file_path="Dockerfile",
        payload="RUN apt-get install -y curl\n",
    )
    assert fix.kind == "replace_line"
    assert fix.file_path == "Dockerfile"


def test_auto_fix_action_frozen() -> None:
    fix = AutoFixAction(kind="add_line", file_path="Dockerfile", payload="USER appuser")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fix.kind = "remove_line"  # type: ignore[misc]


def test_auto_fix_action_equality() -> None:
    a = AutoFixAction(kind="add_line", file_path="Dockerfile", payload="USER appuser")
    b = AutoFixAction(kind="add_line", file_path="Dockerfile", payload="USER appuser")
    assert a == b


def test_auto_fix_action_hashable() -> None:
    a = AutoFixAction(kind="add_line", file_path="Dockerfile", payload="USER appuser")
    b = AutoFixAction(kind="remove_line", file_path="Dockerfile", payload="")
    s = {a, b}
    assert len(s) == 2


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------

def _noop_evaluate(ctx: RepoContext) -> list[Finding]:
    return []


def test_rule_construction() -> None:
    rule = Rule(
        rule_id="dockerfile/missing_user",
        version=1,
        default_severity="warn",
        evaluate=_noop_evaluate,
    )
    assert rule.rule_id == "dockerfile/missing_user"
    assert rule.version == 1
    assert rule.default_severity == "warn"
    assert callable(rule.evaluate)


def test_rule_frozen() -> None:
    rule = Rule(
        rule_id="dockerfile/missing_user",
        version=1,
        default_severity="warn",
        evaluate=_noop_evaluate,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.version = 2  # type: ignore[misc]


def test_rule_equality() -> None:
    r1 = Rule(rule_id="test/rule", version=1, default_severity="info", evaluate=_noop_evaluate)
    r2 = Rule(rule_id="test/rule", version=1, default_severity="info", evaluate=_noop_evaluate)
    # Same callable reference → equal
    assert r1 == r2


def test_rule_hashable() -> None:
    r1 = Rule(rule_id="test/rule_a", version=1, default_severity="warn", evaluate=_noop_evaluate)
    r2 = Rule(rule_id="test/rule_b", version=1, default_severity="warn", evaluate=_noop_evaluate)
    rule_set = {r1, r2}
    assert len(rule_set) == 2
