# pattern: Functional Core
"""Rule: manifest/undeclared_assets — flags Dockerfile COPY/ADD paths that collide
with declared asset mount_paths in rac.yaml.

When a researcher's rac.yaml declares an asset with a mount_path, that path
is intended to be served by the RAC asset pipeline at runtime (not baked into
the image). If the Dockerfile also COPYs a file to the same destination, the
data is baked into the image at build time — likely a mistake that bypasses
the asset pipeline.

This rule requires:
  - ctx.manifest is a dict with an 'assets' key containing a list of asset
    records with 'mount_path' fields.
  - The Dockerfile has one or more COPY or ADD instructions.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule


def _extract_dest_from_copy(value: str) -> str | None:
    """Return the destination path from a COPY/ADD value string.

    COPY/ADD syntax: [--flag ...] <src...> <dest>
    The destination is the last whitespace-delimited token (ignoring flags).
    """
    parts = value.split()
    # Filter out flags (start with --)
    non_flags = [p for p in parts if not p.startswith("--")]
    if len(non_flags) < 2:
        return None
    return non_flags[-1]


def _evaluate(ctx: RepoContext) -> Iterator[Finding]:
    """Fire for each asset mount_path that is also a Dockerfile COPY/ADD destination."""
    if not ctx.manifest:
        return

    assets = ctx.manifest.get("assets")
    if not assets or not isinstance(assets, list):
        return

    # Collect asset mount paths (normalise trailing slashes)
    mount_paths: set[str] = set()
    for asset in assets:
        if isinstance(asset, dict):
            mp = asset.get("mount_path")
            if mp and isinstance(mp, str):
                mount_paths.add(mp.rstrip("/"))

    if not mount_paths:
        return

    from dockerfile_parse import DockerfileParser  # type: ignore[import-untyped]

    encoded = ctx.dockerfile_text.encode("utf-8", errors="replace")
    parser = DockerfileParser(fileobj=io.BytesIO(encoded))

    for item in parser.structure:
        if item["instruction"] not in ("COPY", "ADD"):
            continue

        dest = _extract_dest_from_copy(item["value"])
        if dest is None:
            continue

        dest_normalised = dest.rstrip("/")

        # Check for collision: dest matches or is a prefix of a mount_path, or vice versa
        for mount_path in mount_paths:
            if dest_normalised == mount_path or mount_path.startswith(dest_normalised + "/"):
                line_num = item["startline"] + 1
                yield Finding(
                    rule_id="manifest/undeclared_assets",
                    rule_version=1,
                    severity="warn",
                    title="Dockerfile bakes a declared asset path",
                    detail=(
                        f"The Dockerfile `{item['instruction']}` instruction copies to "
                        f"`{dest}` (line {line_num}), which collides with the asset "
                        f"mount_path `{mount_path}` declared in `rac.yaml`. Assets "
                        "should be served by the RAC asset pipeline at runtime — "
                        "baking them into the image bypasses asset versioning and "
                        "increases image size."
                    ),
                    line_ranges=((line_num, line_num),),
                    file_path=ctx.dockerfile_path,
                    suggested_action="override",
                )
                break  # one finding per COPY/ADD line is enough


RULE = Rule(
    rule_id="manifest/undeclared_assets",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
