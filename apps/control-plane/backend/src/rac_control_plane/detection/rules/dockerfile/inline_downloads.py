# pattern: Functional Core
"""Rule: dockerfile/inline_downloads — detect inline HTTP(S) downloads in RUN instructions.

Parses the Dockerfile via dockerfile-parse (Red Hat-maintained), iterates all RUN
instructions, tokenises subcommands by '&&' / ';' / newline, and flags any command
that downloads content over HTTP(S) at build time without pinning a checksum.

Matched patterns:
  - wget <url>            — where url starts with http:// or https://
  - curl <url>            — unless the curl invocation is --data / --help (not a download)
  - curl -O <url>
  - curl -o <path> <url>
  - fetch <url>
  - lwp-request <url>
  - git clone <url>       — where url starts with http:// or https://

Explicitly excluded:
  - curl --data ...       — POST, not a download
  - curl --help etc.      — informational, not a download

Emit one Finding per matched subcommand with the RUN instruction's 1-based line number.
"""

from __future__ import annotations

import io
import re
import shlex
from collections.abc import Iterator

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule

# HTTP(S) URL prefix pattern
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Flags that indicate curl is NOT performing a download (exclusion list)
_CURL_NO_DOWNLOAD_FLAGS = frozenset([
    "--data", "-d",
    "--data-raw", "--data-binary", "--data-urlencode",
    "--help", "-h",
    "--version", "-V",
    "--head", "-I",
    "--list-only", "-l",
    "--silent", "-s",  # not an exclusion by itself — curl -s CAN download
    "--request", "-X",
    "--user", "-u",
    "--header", "-H",
    "--cookie", "-b",
    "--cookie-jar", "-c",
    "--form", "-F",
    "--upload-file", "-T",
    "--trace",
    "--trace-ascii",
    "--verbose", "-v",
])

# We specifically exclude these flags that mean "this is NOT a file download"
_CURL_EXPLICIT_NO_DOWNLOAD = frozenset([
    "--data", "-d",
    "--data-raw", "--data-binary", "--data-urlencode",
    "--help", "-h",
    "--version", "-V",
    "--upload-file", "-T",
])


def _tokenize_run_value(run_value: str) -> list[str]:
    """Split a RUN instruction value into individual subcommands.

    Handles:
    - Line continuations (trailing backslash before newline)
    - && separators
    - ; separators

    Returns a list of stripped subcommand strings (may still contain leading whitespace).
    """
    # Normalise line continuations: join continuation lines
    normalised = re.sub(r"\\\n\s*", " ", run_value)

    # Split on && and ; but keep subcommand boundaries
    # We split on && first, then ; within each part
    parts: list[str] = []
    for chunk in re.split(r"&&|;", normalised):
        stripped = chunk.strip()
        if stripped:
            parts.append(stripped)
    return parts


def _is_http_url(token: str) -> bool:
    """Return True if token looks like an http(s):// URL."""
    return bool(_HTTP_URL_RE.match(token))


def _find_downloads_in_subcommand(subcommand: str) -> str | None:  # noqa: C901
    """Analyse a single shell subcommand for inline downloads.

    Returns the subcommand string if a download is detected, else None.
    """
    # Try to tokenise via shlex; fall back to whitespace split on error
    try:
        tokens = shlex.split(subcommand)
    except ValueError:
        tokens = subcommand.split()

    if not tokens:
        return None

    cmd = tokens[0].lstrip("\\")  # strip leading backslash from &&-joined lines

    # --- wget ---
    if cmd in ("wget", "/usr/bin/wget", "/usr/local/bin/wget"):
        # Any positional argument that looks like a URL is a download
        for tok in tokens[1:]:
            if _is_http_url(tok):
                return subcommand
        return None

    # --- curl ---
    if cmd in ("curl", "/usr/bin/curl", "/usr/local/bin/curl"):
        # Check for explicit non-download flags first
        for tok in tokens[1:]:
            if tok in _CURL_EXPLICIT_NO_DOWNLOAD:
                return None
        # Look for an http(s) URL in the argument list
        for tok in tokens[1:]:
            if _is_http_url(tok):
                return subcommand
        return None

    # --- fetch (FreeBSD/OpenBSD) ---
    if cmd in ("fetch", "/usr/bin/fetch"):
        for tok in tokens[1:]:
            if _is_http_url(tok):
                return subcommand
        return None

    # --- lwp-request (libwww-perl) ---
    if cmd in ("lwp-request", "/usr/bin/lwp-request"):
        for tok in tokens[1:]:
            if _is_http_url(tok):
                return subcommand
        return None

    # --- git clone ---
    if cmd in ("git", "/usr/bin/git"):
        # git clone <http-url>
        if len(tokens) > 2 and tokens[1] == "clone":
            for tok in tokens[2:]:
                if _is_http_url(tok):
                    return subcommand
        return None

    return None


def _evaluate(ctx: RepoContext) -> Iterator[Finding]:
    """Evaluate the dockerfile/inline_downloads rule against a RepoContext."""
    from dockerfile_parse import DockerfileParser

    parser = DockerfileParser(fileobj=io.BytesIO(ctx.dockerfile_text.encode("utf-8", errors="replace")))

    for item in parser.structure:
        if item["instruction"] != "RUN":
            continue

        # 1-based line number
        line_num: int = item["startline"] + 1
        run_value: str = item["value"]

        subcommands = _tokenize_run_value(run_value)
        for sub in subcommands:
            matched = _find_downloads_in_subcommand(sub)
            if matched is not None:
                # Truncate command for display safety
                display_cmd = matched if len(matched) <= 200 else matched[:197] + "..."
                yield Finding(
                    rule_id="dockerfile/inline_downloads",
                    rule_version=1,
                    severity="warn",
                    title="Inline download in Dockerfile",
                    detail=(
                        f"The command `{display_cmd}` downloads content at build time "
                        "without pinning a checksum; this makes builds non-reproducible "
                        "and invites supply-chain compromise. Consider pre-fetching the "
                        "asset and verifying its sha256, or using a package manager with "
                        "lock files."
                    ),
                    line_ranges=((line_num, line_num),),
                    file_path=ctx.dockerfile_path,
                    suggested_action="override",
                    auto_fix=None,
                )


RULE = Rule(
    rule_id="dockerfile/inline_downloads",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
