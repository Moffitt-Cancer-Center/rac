# pattern: Functional Core
"""Rule: dockerfile/root_user — fires if the *last* USER instruction is root or UID 0.

Explicitly setting USER root (or USER 0) negates any earlier non-root USER
instruction and runs the container as the most privileged user.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule

# Values that constitute the root user in a USER instruction
_ROOT_USERS = frozenset(["root", "0", "root:root", "0:0"])


def _evaluate(ctx: RepoContext) -> Iterator[Finding]:
    """Fire if the last USER instruction in the Dockerfile resolves to root."""
    from dockerfile_parse import DockerfileParser

    parser = DockerfileParser(fileobj=io.BytesIO(ctx.dockerfile_text.encode("utf-8", errors="replace")))

    user_instructions = [
        item for item in parser.structure if item["instruction"] == "USER"
    ]

    if not user_instructions:
        # dockerfile/missing_user handles this case — don't double-fire
        return

    last_user = user_instructions[-1]
    user_value = last_user["value"].strip()

    if user_value in _ROOT_USERS:
        line_num = last_user["startline"] + 1
        yield Finding(
            rule_id="dockerfile/root_user",
            rule_version=1,
            severity="warn",
            title="Container runs as root",
            detail=(
                f"The last `USER` instruction sets the container user to `{user_value}` "
                "(root). Running containers as root increases risk if the container is "
                "compromised. Switch to a non-root user: `USER appuser` or a numeric "
                "UID such as `USER 1001`."
            ),
            line_ranges=((line_num, line_num),),
            file_path=ctx.dockerfile_path,
            suggested_action="override",
        )


RULE = Rule(
    rule_id="dockerfile/root_user",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
