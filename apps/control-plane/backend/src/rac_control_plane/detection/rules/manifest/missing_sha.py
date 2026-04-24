# pattern: Functional Core
"""Rule: manifest/missing_sha — flags external_url assets with missing or invalid sha256.

External URL assets MUST declare a 64-character hex sha256 so integrity can
be verified at fetch time. This rule fires at detection time (before any
network fetch occurs) so the researcher is warned immediately rather than
discovering the omission when the pipeline tries to verify the download.
"""

from __future__ import annotations

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule

RULE_ID = "manifest/missing_sha"
RULE_VERSION = 1


def _is_valid_sha256(s: str) -> bool:
    """Return True iff s is exactly 64 lowercase or uppercase hex characters."""
    return len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s)


def _evaluate(ctx: RepoContext) -> list[Finding]:
    """For every external_url asset in the manifest, check sha256 is present AND
    exactly 64 hex chars. Missing/invalid → severity='error' Finding.
    """
    findings: list[Finding] = []
    manifest = ctx.manifest
    if not manifest or "assets" not in manifest:
        return findings

    raw_assets = manifest.get("assets", [])
    if not isinstance(raw_assets, list):
        return findings

    for asset in raw_assets:
        if not isinstance(asset, dict):
            continue
        if asset.get("kind") != "external_url":
            continue

        name = asset.get("name", "<unknown>")
        sha = asset.get("sha256", "")
        # sha may be None if it came from a dict with explicit None
        if sha is None:
            sha = ""

        if not sha or not _is_valid_sha256(sha):
            findings.append(
                Finding(
                    rule_id=RULE_ID,
                    rule_version=RULE_VERSION,
                    severity="error",
                    title=f"External asset '{name}' has missing or invalid sha256",
                    detail=(
                        f"External URL assets MUST declare a 64-character hex sha256 "
                        f"so integrity can be verified at fetch time. "
                        f"Asset '{name}' has: {sha!r}"
                    ),
                    file_path="rac.yaml",
                    suggested_action="override",
                )
            )

    return findings


RULE = Rule(
    rule_id=RULE_ID,
    version=RULE_VERSION,
    default_severity="error",
    evaluate=_evaluate,
)
