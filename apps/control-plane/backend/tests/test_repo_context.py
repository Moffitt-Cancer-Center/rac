"""Tests for the RepoContext builder.

Verifies:
- Populated RepoContext from a local fixture repo (skipping git clone via _prebuilt_repo_root)
- ctx.read() resolves valid files, rejects ../ traversal
- Missing Dockerfile → RepoContextError
- Manifest parsed when rac.yaml present; None when absent
- scan_repo_tree correctly skips .git/
"""

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from rac_control_plane.detection.contracts import RepoContext
from rac_control_plane.detection.repo_context import (
    RepoContextError,
    build_repo_context,
    scan_repo_tree,
)


# ---------------------------------------------------------------------------
# Stub Submission ORM object
# ---------------------------------------------------------------------------

class _FakeSubmission:
    """Minimal duck-typed substitute for the ORM Submission row."""

    def __init__(
        self,
        *,
        github_repo_url: str = "https://github.com/test/repo",
        git_ref: str = "main",
        dockerfile_path: str = "Dockerfile",
        pi_principal_id: UUID | None = None,
        paper_title: str = "Test Paper",
        agent_kind: str | None = None,
        manifest: Any = None,
    ) -> None:
        self.id = uuid4()
        self.github_repo_url = github_repo_url
        self.git_ref = git_ref
        self.dockerfile_path = dockerfile_path
        self.pi_principal_id = pi_principal_id or uuid4()
        self.paper_title = paper_title
        self.agent_kind = agent_kind
        self.manifest = manifest


# ---------------------------------------------------------------------------
# Helper: build a tiny fixture repo in tmp_path
# ---------------------------------------------------------------------------

def _build_fixture_repo(tmp_path: Path) -> Path:
    """Create a minimal repo directory structure for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM python:3.12\nRUN echo hello\n")
    (repo / "app.py").write_text("print('hello world')\n")
    (repo / "rac.yaml").write_text("name: test-app\nversion: 1\n")
    # subdir
    subdir = repo / "src"
    subdir.mkdir()
    (subdir / "main.py").write_text("# main\n")
    # .git dir — should be excluded
    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return repo


# ---------------------------------------------------------------------------
# Tests for scan_repo_tree
# ---------------------------------------------------------------------------

def test_scan_repo_tree_excludes_git(tmp_path: Path) -> None:
    """scan_repo_tree must not include .git/ files."""
    repo = _build_fixture_repo(tmp_path)
    files = scan_repo_tree(repo)
    paths = {f.path for f in files}
    assert not any(".git" in p for p in paths), f".git files found: {paths}"


def test_scan_repo_tree_includes_all_files(tmp_path: Path) -> None:
    """scan_repo_tree includes Dockerfile, app.py, rac.yaml, src/main.py."""
    repo = _build_fixture_repo(tmp_path)
    files = scan_repo_tree(repo)
    paths = {f.path for f in files}
    assert "Dockerfile" in paths
    assert "app.py" in paths
    assert "rac.yaml" in paths
    assert "src/main.py" in paths


def test_scan_repo_tree_size_bytes(tmp_path: Path) -> None:
    """RepoFile.size_bytes matches actual file size."""
    repo = tmp_path / "repo"
    repo.mkdir()
    content = b"hello world"
    (repo / "file.txt").write_bytes(content)
    files = scan_repo_tree(repo)
    assert len(files) == 1
    assert files[0].size_bytes == len(content)


# ---------------------------------------------------------------------------
# Tests for build_repo_context (using _prebuilt_repo_root to skip clone)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_repo_context_populates_fields(tmp_path: Path) -> None:
    """build_repo_context returns a correctly populated RepoContext."""
    repo = _build_fixture_repo(tmp_path)
    submission = _FakeSubmission()

    ctx = await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)

    assert isinstance(ctx, RepoContext)
    assert ctx.repo_root == repo
    assert isinstance(ctx.submission_id, UUID)
    assert ctx.dockerfile_path == "Dockerfile"
    assert "FROM python:3.12" in ctx.dockerfile_text
    assert len(ctx.files) > 0
    # rac.yaml present → manifest parsed
    assert ctx.manifest is not None
    assert ctx.manifest.get("name") == "test-app"


@pytest.mark.asyncio
async def test_build_repo_context_no_rac_yaml(tmp_path: Path) -> None:
    """manifest is None when rac.yaml is absent."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM python:3.12\n")
    submission = _FakeSubmission()

    ctx = await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)

    assert ctx.manifest is None


@pytest.mark.asyncio
async def test_build_repo_context_missing_dockerfile_raises(tmp_path: Path) -> None:
    """Missing Dockerfile path → RepoContextError."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # No Dockerfile created
    submission = _FakeSubmission(dockerfile_path="Dockerfile")

    with pytest.raises(RepoContextError, match="Dockerfile not found"):
        await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)


@pytest.mark.asyncio
async def test_build_repo_context_custom_dockerfile_path(tmp_path: Path) -> None:
    """Custom dockerfile_path (e.g. docker/Dockerfile) is read correctly."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker").mkdir()
    (repo / "docker" / "Dockerfile").write_text("FROM ubuntu:22.04\n")
    submission = _FakeSubmission(dockerfile_path="docker/Dockerfile")

    ctx = await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)

    assert "FROM ubuntu:22.04" in ctx.dockerfile_text
    assert ctx.dockerfile_path == "docker/Dockerfile"


@pytest.mark.asyncio
async def test_build_repo_context_submission_metadata(tmp_path: Path) -> None:
    """submission_metadata contains pi_principal_id and paper_title."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM python:3.12\n")
    pi_id = uuid4()
    submission = _FakeSubmission(pi_principal_id=pi_id, paper_title="Cancer Study 2026")

    ctx = await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)

    assert ctx.submission_metadata["pi_principal_id"] == str(pi_id)
    assert ctx.submission_metadata["paper_title"] == "Cancer Study 2026"


# ---------------------------------------------------------------------------
# Tests for RepoContext.read (path-traversal guard — also tested in contracts)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ctx_read_valid_file(tmp_path: Path) -> None:
    """ctx.read('app.py') returns the file content."""
    repo = _build_fixture_repo(tmp_path)
    submission = _FakeSubmission()
    ctx = await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)

    content = ctx.read("app.py")
    assert b"hello world" in content


@pytest.mark.asyncio
async def test_ctx_read_traversal_rejected(tmp_path: Path) -> None:
    """ctx.read('../../etc/passwd') raises ValueError."""
    repo = _build_fixture_repo(tmp_path)
    submission = _FakeSubmission()
    ctx = await build_repo_context(submission, tmp_path, _prebuilt_repo_root=repo)

    with pytest.raises(ValueError):
        ctx.read("../../etc/passwd")


# ---------------------------------------------------------------------------
# Test: clone failure raises RepoContextError (mocked subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_repo_context_clone_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git clone returning non-zero → RepoContextError."""
    import subprocess as sp

    class FakeResult:
        returncode = 1
        stderr = "fatal: repository not found"
        stdout = ""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeResult())

    submission = _FakeSubmission()
    with pytest.raises(RepoContextError, match="clone failed"):
        await build_repo_context(submission, tmp_path)
