# pattern: Functional Core
"""Rule evaluator — pure function that calls each rule over a RepoContext.

All I/O is prohibited here. Exceptions inside a rule are caught and converted
to synthetic warn Findings so operator breakage is surfaced without aborting
the full evaluation run.
"""

import structlog
from collections.abc import Iterable

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule

logger = structlog.get_logger(__name__)


def run_all(rules: Iterable[Rule], ctx: RepoContext) -> list[Finding]:
    """Run all rules over ctx, collecting findings.

    Each rule.evaluate(ctx) is called. Exceptions inside a rule produce a
    synthetic Finding with severity='warn' and title='rule error' so operators
    can see the breakage without losing findings from other rules.

    Duplicates are preserved as-is (AC4.6): if the same rule emits two
    Findings for different lines, both appear in the result.

    Args:
        rules: Iterable of Rule objects to evaluate.
        ctx: Immutable repository snapshot to evaluate against.

    Returns:
        List of all Finding objects, in rule-then-finding order.
    """
    results: list[Finding] = []

    for rule in rules:
        try:
            found = list(rule.evaluate(ctx))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "rule_evaluate_exception",
                rule_id=rule.rule_id,
                error=str(exc),
                exc_info=True,
            )
            # Synthetic finding so the breakage shows up in the UI
            results.append(
                Finding(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    severity="warn",
                    title="rule error",
                    detail=(
                        f"Rule `{rule.rule_id}` raised an unhandled exception during "
                        f"evaluation: {type(exc).__name__}: {exc}"
                    ),
                )
            )
            continue

        results.extend(found)

    return results
