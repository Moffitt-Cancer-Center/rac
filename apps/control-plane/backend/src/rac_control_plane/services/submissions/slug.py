# pattern: Functional Core
"""Submission slug derivation.

Pure functions to derive human-readable, unique slugs from paper titles
and GitHub repo URLs.
"""

import re
import urllib.parse
from collections.abc import Iterable
from pathlib import Path


def _normalize_title(title: str) -> str:
    """Normalize title to slug format.

    Rules:
    - Lowercase
    - Non-alphanumeric (except hyphens) -> hyphens
    - Multiple consecutive hyphens -> single hyphen
    - Strip leading/trailing hyphens
    - Max 40 chars

    Args:
        title: Paper title or similar string.

    Returns:
        Normalized slug component.
    """
    # Lowercase
    slug = title.lower()

    # Replace non-alphanumeric with hyphens (except spaces, which become hyphens)
    slug = re.sub(r"[^a-z0-9-]", "-", slug)

    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)

    # Strip leading/trailing hyphens
    slug = slug.strip("-")

    # Truncate to 40 chars
    slug = slug[:40]

    return slug


def _get_repo_basename(github_url: str) -> str:
    """Extract repo name from GitHub URL.

    Examples:
        https://github.com/user/my-repo -> my-repo
        git@github.com:user/my-repo.git -> my-repo
        https://github.com/user/my-repo.git -> my-repo

    Args:
        github_url: Full GitHub repository URL.

    Returns:
        Repository basename.
    """
    # Parse as URL or SSH
    if github_url.startswith("git@"):
        # SSH: git@github.com:user/repo.git
        path = github_url.split(":", 1)[1]
    else:
        # HTTPS: parse the path
        parsed = urllib.parse.urlparse(github_url)
        path = parsed.path

    # Remove .git suffix if present
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]

    # Extract basename (last path component)
    basename = Path(path).name

    return basename


def derive_slug(
    paper_title: str | None,
    github_repo: str,
    existing_slugs: Iterable[str],
) -> str:
    """Derive a unique slug for a submission.

    Priority:
    1. If paper_title provided and unique -> normalize it.
    2. Otherwise -> use repo basename.
    3. If still colliding -> append -N (smallest N >= 2) until unique.

    Rules:
    - Result matches regex ^[a-z0-9]+(-[a-z0-9]+)*$ (lowercase, alphanumeric, hyphens)
    - Max 40 chars
    - No leading/trailing hyphens
    - Unique against existing_slugs set

    Args:
        paper_title: Optional paper title to derive slug from.
        github_repo: GitHub repository URL (fallback if title missing).
        existing_slugs: Iterable of already-used slugs (set or list).

    Returns:
        Unique slug.
    """
    existing = set(existing_slugs)

    # Try paper title first if provided
    if paper_title:
        candidate = _normalize_title(paper_title)
        if candidate and candidate not in existing:
            return candidate

    # Fall back to repo basename
    repo_base = _get_repo_basename(github_repo)
    candidate = _normalize_title(repo_base)

    # Ensure candidate is valid (non-empty, no leading/trailing hyphens)
    if not candidate:
        # Last resort: use a default
        candidate = "app"

    # If candidate not in existing, return it
    if candidate not in existing:
        return candidate

    # Collision: append -N until unique
    n = 2
    while True:
        suffixed = f"{candidate}-{n}"
        if suffixed not in existing:
            return suffixed
        n += 1

        # Safety: don't loop forever (though in practice N will be small)
        if n > 1000:
            raise ValueError(f"Cannot derive unique slug after 1000 attempts for {candidate}")
