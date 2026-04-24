# pattern: Functional Core
"""Pure resolution logic for detection findings.

Determines whether all findings requiring user attention have been resolved
by a researcher decision, allowing the submission to return to awaiting_scan.
"""

from __future__ import annotations

from typing import Any


# Decision values that constitute a positive resolution of an error finding
_RESOLVING_DECISIONS = frozenset(["accept", "override", "auto_fix"])


def needs_user_action_resolved(findings_with_decisions: list[dict[str, Any]]) -> bool:
    """Return True iff every severity='error' finding has a resolving decision.

    A resolving decision is one of: accept, override, auto_fix.
    'dismiss' does NOT resolve an error finding — it is for advisory/warn
    findings only.

    Severity='warn' or 'info' findings with no decision do NOT block resolution.

    Args:
        findings_with_decisions: List of dicts as returned by
            detection_finding_store.list_findings_with_latest_decision.
            Expected keys: 'severity', 'latest_decision'.

    Returns:
        True if all error findings are resolved (or no error findings exist).
        False if any error finding lacks a resolving decision.
    """
    for finding in findings_with_decisions:
        if finding.get("severity") != "error":
            continue
        decision = finding.get("latest_decision")
        if decision not in _RESOLVING_DECISIONS:
            return False
    return True
