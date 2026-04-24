# pattern: Functional Core
"""Rule: dockerfile/missing_user — fires if Dockerfile has no USER instruction.

A container image with no USER instruction defaults to running as root, which
expands the blast radius of a container escape. Security best practice is to
add a non-root USER before the ENTRYPOINT/CMD.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule


def _evaluate(ctx: RepoContext) -> Iterator[Finding]:
    """Fire if no USER instruction is present in the Dockerfile."""
    from dockerfile_parse import DockerfileParser

    parser = DockerfileParser(fileobj=io.BytesIO(ctx.dockerfile_text.encode("utf-8", errors="replace")))

    has_user = any(item["instruction"] == "USER" for item in parser.structure)

    if not has_user:
        yield Finding(
            rule_id="dockerfile/missing_user",
            rule_version=1,
            severity="warn",
            title="No USER instruction in Dockerfile",
            detail=(
                "The Dockerfile does not set a `USER` instruction. The container will "
                "run as root by default, which increases the blast radius if the "
                "container is compromised. Add a non-root `USER` instruction before "
                "`ENTRYPOINT` or `CMD`."
            ),
            file_path=ctx.dockerfile_path,
            suggested_action="override",
        )


RULE = Rule(
    rule_id="dockerfile/missing_user",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
