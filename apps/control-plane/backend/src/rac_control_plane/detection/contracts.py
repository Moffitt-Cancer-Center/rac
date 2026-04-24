"""Detection contracts — Rule, Finding, RepoContext.

Type-only module: pure dataclasses and type aliases.
No FCIS tag: type-only modules are exempt per Phase 4 policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping
from uuid import UUID


Severity = Literal["info", "warn", "error"]


@dataclass(frozen=True)
class RepoFile:
    """Lightweight record of a file in the cloned repo."""

    path: str  # relative from repo root
    size_bytes: int
    # We intentionally do NOT include the full content.
    # Rules that need content read it explicitly via RepoContext.read().


@dataclass(frozen=True)
class RepoContext:
    """Immutable snapshot of everything a detection rule needs.

    Built once per submission by the Imperative Shell (repo_context.py).
    All rules run over it deterministically without I/O.
    """

    repo_root: Path
    submission_id: UUID
    dockerfile_path: str
    dockerfile_text: str
    files: tuple[RepoFile, ...]  # all non-gitignored tracked + untracked files
    manifest: Mapping[str, object] | None  # parsed rac.yaml as dict, or None if absent
    submission_metadata: Mapping[str, object]  # pi_principal_id, paper_title, agent_kind

    def read(self, path: str) -> bytes:
        """Read a file from the cloned repo.

        Raises:
            ValueError: If path contains '..' or is absolute (traversal guard).
            FileNotFoundError: If the file does not exist in the repo.
        """
        # Reject absolute paths
        if Path(path).is_absolute():
            raise ValueError(f"Absolute path not allowed: {path!r}")

        # Resolve to catch any sneaky '..' sequences
        target = (self.repo_root / path).resolve()
        repo_resolved = self.repo_root.resolve()

        # Ensure resolved path stays inside repo_root
        try:
            target.relative_to(repo_resolved)
        except ValueError:
            raise ValueError(f"Path traversal detected: {path!r}")

        return target.read_bytes()


@dataclass(frozen=True)
class AutoFixAction:
    """A safe, programmatic fix action for a finding."""

    kind: Literal["replace_line", "add_line", "remove_line", "apply_patch"]
    file_path: str
    payload: str  # new content / patch


@dataclass(frozen=True)
class Finding:
    """A single detection finding emitted by a rule."""

    rule_id: str  # e.g. "dockerfile/inline_downloads" — no whitespace
    rule_version: int  # incremented by author when rule logic changes
    severity: Severity  # "warn" by default
    title: str  # short, user-facing
    detail: str  # markdown-safe explanation
    line_ranges: tuple[tuple[int, int], ...] = ()  # Dockerfile line refs (1-based)
    file_path: str | None = None  # file path the finding concerns
    suggested_action: Literal["accept", "override", "auto_fix", "dismiss"] | None = None
    auto_fix: AutoFixAction | None = None  # if a safe programmatic fix exists

    def __post_init__(self) -> None:
        """Validate rule_id has no whitespace."""
        if any(c.isspace() for c in self.rule_id):
            raise ValueError(f"Finding.rule_id must not contain whitespace: {self.rule_id!r}")


@dataclass(frozen=True)
class Rule:
    """A detection rule with metadata and a pure evaluator function."""

    rule_id: str
    version: int
    default_severity: Severity
    evaluate: Callable[[RepoContext], Iterable[Finding]]
