# pattern: Functional Core
"""Rule: repo/secrets_in_repo — regex sweep for common secret patterns in text files.

Scans files with recognised text extensions (≤ 1 MB) for patterns commonly
associated with leaked secrets:
  - AWS Access Key IDs (AKIA…)
  - Azure Storage Account keys (base64-encoded 64-byte value after AccountKey=)
  - GitHub Personal Access Tokens (ghp_, gho_, ghs_, ghr_, github_pat_)
  - Private key PEM headers (-----BEGIN * PRIVATE KEY-----)

IMPORTANT: Matched values are NEVER included verbatim in the Finding detail.
Only the first 4 characters are shown followed by '***' to prevent the
Control Plane DB from becoming a secondary secret store.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import structlog

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule

logger = structlog.get_logger(__name__)

# Max file size to scan (1 MB)
_MAX_SCAN_BYTES = 1 * 1024 * 1024

# File extensions to scan
_TEXT_EXTENSIONS = frozenset([
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".rs",
    ".env", ".env.example", ".env.local",
    ".yaml", ".yml", ".json",
    ".sh", ".bash", ".zsh",
    ".rb", ".php", ".java", ".cs", ".cpp", ".c", ".h",
    ".toml", ".ini", ".cfg", ".conf",
    ".tf", ".tfvars",
    ".md", ".txt",
])

# Secret patterns: (name, compiled_regex)
# Each regex must have a named capture group `secret` containing the sensitive value.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "AWS Access Key ID",
        re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])", re.ASCII),
    ),
    (
        "Azure Storage Account Key",
        re.compile(
            r"AccountKey=([A-Za-z0-9+/]{86}==)",
            re.IGNORECASE,
        ),
    ),
    (
        "GitHub Personal Access Token",
        re.compile(
            r"(ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|ghs_[A-Za-z0-9]{36}"
            r"|ghr_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82})",
        ),
    ),
    (
        "Private Key PEM header",
        re.compile(
            r"-----BEGIN\s+(?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
        ),
    ),
]


def _truncate_secret(value: str) -> str:
    """Return first 4 chars + '***' for display — never expose full secret."""
    prefix = value[:4] if len(value) >= 4 else value
    return f"{prefix}***"


def _should_scan(path: str, size_bytes: int) -> bool:
    """Return True if this file should be included in the secret scan."""
    if size_bytes > _MAX_SCAN_BYTES:
        return False
    ext = Path(path).suffix.lower()
    # Also scan files with no extension if they look like dotfiles (.env, etc.)
    name = Path(path).name.lower()
    if name.startswith(".env"):
        return True
    return ext in _TEXT_EXTENSIONS


def _evaluate(ctx: RepoContext) -> Iterator[Finding]:
    """Scan text files for common secret patterns."""
    for repo_file in ctx.files:
        if not _should_scan(repo_file.path, repo_file.size_bytes):
            continue

        try:
            raw_bytes = ctx.read(repo_file.path)
        except (FileNotFoundError, ValueError, OSError) as exc:
            logger.debug("secrets_scan_read_error", path=repo_file.path, error=str(exc))
            continue

        # Decode leniently
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue

        for pattern_name, pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                # Extract the secret value (first group or full match)
                secret_value = match.group(1) if match.lastindex else match.group(0)
                truncated = _truncate_secret(secret_value)

                # Compute line number (1-based)
                line_num = text[: match.start()].count("\n") + 1

                yield Finding(
                    rule_id="repo/secrets_in_repo",
                    rule_version=1,
                    severity="warn",
                    title="Potential secret in repository",
                    detail=(
                        f"Possible {pattern_name} detected in `{repo_file.path}` "
                        f"(line {line_num}): `{truncated}`. If this is a real "
                        "credential, rotate it immediately and remove it from the "
                        "repository history. Use environment variables or a secrets "
                        "manager instead."
                    ),
                    line_ranges=((line_num, line_num),),
                    file_path=repo_file.path,
                    suggested_action="override",
                )


RULE = Rule(
    rule_id="repo/secrets_in_repo",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
