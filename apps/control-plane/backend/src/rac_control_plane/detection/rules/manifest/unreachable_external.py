# pattern: Functional Core
"""Rule: manifest/unreachable_external — flags structural issues in external asset URLs.

DESIGN NOTE: The name 'unreachable_external' is somewhat misleading. Actual
network reachability checks require I/O, which violates the Functional Core
principle for rule modules. This rule therefore ONLY flags *structural* issues:
  - External asset URLs that are missing a sha256 integrity field.
  - External asset URLs that cannot be parsed as valid URLs.

The actual reachability check (HTTP HEAD request to verify the URL responds)
is implemented as a separate Shell service (`services/manifest/reachability_check.py`,
Phase 8). That service emits synthetic Findings via the detection_finding store.

See: docs/implementation-plans/2026-04-23-rac-v1/README.md — "Approved design deviations"
     Phase 4, Task 5 — FCIS purity split for unreachable_external rule.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule


def _is_valid_url(url: str) -> bool:
    """Return True if url has a valid scheme and netloc."""
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme in ("http", "https", "ftp", "ftps") and parsed.netloc)
    except Exception:  # noqa: BLE001
        return False


def _evaluate(ctx: RepoContext) -> Iterator[Finding]:
    """Flag structural issues (missing sha256, unparseable URL) in external assets."""
    if not ctx.manifest:
        return

    assets = ctx.manifest.get("assets")
    if not assets or not isinstance(assets, list):
        return

    for idx, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue

        url = asset.get("url")
        if not url or not isinstance(url, str):
            continue

        # Only flag 'external' assets (those with explicit URLs, not local paths)
        if not url.startswith(("http://", "https://", "ftp://", "ftps://")):
            continue

        asset_name = asset.get("name") or f"asset[{idx}]"

        # Check 1: unparseable URL
        if not _is_valid_url(url):
            yield Finding(
                rule_id="manifest/unreachable_external",
                rule_version=1,
                severity="warn",
                title="Unparseable external asset URL",
                detail=(
                    f"The external asset `{asset_name}` has a URL that cannot be "
                    f"parsed as a valid HTTP(S)/FTP URL: `{url!r}`. Verify the URL "
                    "is correct in `rac.yaml`."
                ),
                file_path="rac.yaml",
                suggested_action="override",
            )
            continue

        # Check 2: missing sha256 integrity field
        sha256 = asset.get("sha256") or asset.get("checksum") or asset.get("integrity")
        if not sha256:
            yield Finding(
                rule_id="manifest/unreachable_external",
                rule_version=1,
                severity="warn",
                title="External asset missing sha256 checksum",
                detail=(
                    f"The external asset `{asset_name}` (`{url}`) in `rac.yaml` does "
                    "not specify a `sha256` checksum. Without an integrity check, the "
                    "build is vulnerable to supply-chain substitution. Add a `sha256` "
                    "field with the hex digest of the expected file."
                ),
                file_path="rac.yaml",
                suggested_action="override",
            )


RULE = Rule(
    rule_id="manifest/unreachable_external",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
