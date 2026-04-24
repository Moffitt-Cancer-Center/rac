# pattern: Imperative Shell
"""RepoContext builder — git clone, file scan, manifest parse.

Materialises an immutable RepoContext snapshot once per submission.
All network I/O (git clone) and filesystem I/O happens here; rule modules
never perform I/O directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import UUID

import structlog
import yaml

from rac_control_plane.data.models import Submission
from rac_control_plane.detection.contracts import RepoContext, RepoFile

logger = structlog.get_logger(__name__)


class RepoContextError(Exception):
    """Raised when the RepoContext cannot be built (clone failure, missing Dockerfile)."""


def scan_repo_tree(repo_root: Path) -> list[RepoFile]:
    """Walk *repo_root*, skip `.git/`, return one RepoFile per file.

    Pure-ish: only filesystem reads, no network I/O. Factored out for testability.

    Args:
        repo_root: Absolute path to the checked-out repository directory.

    Returns:
        List of RepoFile records sorted by path.
    """
    files: list[RepoFile] = []
    for p in sorted(repo_root.rglob("*")):
        # Skip .git directory tree entirely
        try:
            p.relative_to(repo_root / ".git")
            continue
        except ValueError:
            pass

        if p.is_file():
            files.append(RepoFile(path=str(p.relative_to(repo_root)), size_bytes=p.stat().st_size))
    return files


async def build_repo_context(
    submission: Submission,
    workdir: Path,
    *,
    git_binary: str = "git",
    _prebuilt_repo_root: Path | None = None,
) -> RepoContext:
    """Build a RepoContext by cloning the submission repo and scanning its contents.

    Args:
        submission: ORM Submission row (must have github_repo_url, git_ref, dockerfile_path,
                    id, pi_principal_id, manifest).
        workdir: Scratch directory. A subdirectory ``repo/`` will be created here.
        git_binary: Path to git executable (injectable for testing).
        _prebuilt_repo_root: If provided, skip cloning and use this path directly.
                             Used in tests to avoid real git operations.

    Returns:
        Populated RepoContext.

    Raises:
        RepoContextError: If git clone fails or dockerfile_path is missing.
    """
    if _prebuilt_repo_root is not None:
        repo_root = _prebuilt_repo_root
    else:
        repo_root = workdir / "repo"

        # Step 1: git clone --depth 1 --branch <ref> <url> <workdir>/repo
        clone_cmd = [
            git_binary,
            "clone",
            "--depth", "1",
            "--branch", submission.git_ref,
            submission.github_repo_url,
            str(repo_root),
        ]
        logger.info(
            "repo_context_cloning",
            url=submission.github_repo_url,
            ref=submission.git_ref,
        )
        result = subprocess.run(  # noqa: S603
            clone_cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RepoContextError(
                f"clone failed: {result.stderr.strip() or result.stdout.strip()}"
            )

    # Step 2: Walk filesystem
    files = scan_repo_tree(repo_root)

    # Step 3: Read Dockerfile text
    dockerfile_full = repo_root / submission.dockerfile_path
    if not dockerfile_full.exists():
        raise RepoContextError(
            f"Dockerfile not found at {submission.dockerfile_path!r} in cloned repo"
        )
    dockerfile_text = dockerfile_full.read_text(encoding="utf-8", errors="replace")

    # Step 4: Parse rac.yaml if present
    rac_yaml_path = repo_root / "rac.yaml"
    manifest: dict[str, object] | None = None
    if rac_yaml_path.exists():
        try:
            raw = yaml.safe_load(rac_yaml_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                manifest = raw
        except yaml.YAMLError as exc:
            logger.warning("rac_yaml_parse_error", error=str(exc))

    # Step 5: Build submission_metadata
    submission_metadata: dict[str, object] = {
        "pi_principal_id": str(submission.pi_principal_id),
        "paper_title": getattr(submission, "paper_title", None),
        "agent_kind": getattr(submission, "agent_kind", None),
    }

    # Step 6: Construct and return
    return RepoContext(
        repo_root=repo_root,
        submission_id=UUID(str(submission.id)),
        dockerfile_path=submission.dockerfile_path,
        dockerfile_text=dockerfile_text,
        files=tuple(files),
        manifest=manifest,
        submission_metadata=submission_metadata,
    )
