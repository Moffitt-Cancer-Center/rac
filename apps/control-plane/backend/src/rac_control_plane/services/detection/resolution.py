# pattern: Functional Core
"""Pure resolution logic for detection findings.

Determines whether all findings requiring user attention have been resolved
by a researcher decision, allowing the submission to return to awaiting_scan.
"""

from __future__ import annotations

from typing import Any  # noqa: UP035 — Any not in collections.abc

# Decision values that constitute a positive resolution of an error finding
_RESOLVING_DECISIONS = frozenset(["accept", "override", "auto_fix"])


def _extract_decision_value(finding: dict[str, Any]) -> str | None:
    """Extract the decision value from a finding dict.

    Supports both the new nested shape (``decision: {decision: "accept", ...}``)
    returned by ``list_findings_with_latest_decision`` and the flat shape
    (``latest_decision: "accept"``) used in legacy callers / direct test dicts.
    """
    nested = finding.get("decision")
    if nested is not None and isinstance(nested, dict):
        value = nested.get("decision")
        return str(value) if value is not None else None
    # Legacy / test flat key
    value = finding.get("latest_decision")
    return str(value) if value is not None else None


def needs_user_action_resolved(findings_with_decisions: list[dict[str, Any]]) -> bool:
    """Return True iff every severity='error' finding has a resolving decision.

    A resolving decision is one of: accept, override, auto_fix.
    'dismiss' does NOT resolve an error finding — it is for advisory/warn
    findings only.

    Severity='warn' or 'info' findings with no decision do NOT block resolution.

    Args:
        findings_with_decisions: List of dicts as returned by
            detection_finding_store.list_findings_with_latest_decision.
            Expected keys: 'severity', and either 'decision' (nested dict) or
            'latest_decision' (flat str, legacy).

    Returns:
        True if all error findings are resolved (or no error findings exist).
        False if any error finding lacks a resolving decision.
    """
    for finding in findings_with_decisions:
        if finding.get("severity") != "error":
            continue
        decision = _extract_decision_value(finding)
        if decision not in _RESOLVING_DECISIONS:
            return False
    return True
