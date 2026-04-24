# pattern: Functional Core
"""PI (Principal Investigator) validation — pure logic layer.

Takes a GraphUser (or None) and decides whether the PI is valid for a
new RAC submission.  No I/O; no side effects.
"""

from dataclasses import dataclass
from typing import Literal

from rac_control_plane.services.ownership.graph_gateway import GraphUser


@dataclass(frozen=True)
class Ok:
    """PI is a valid, active Entra principal."""


@dataclass(frozen=True)
class Invalid:
    """PI is invalid for the given reason."""

    reason: Literal["not_found", "account_disabled"]


ValidationResult = Ok | Invalid


def is_valid_pi(user: GraphUser | None) -> ValidationResult:
    """Determine whether a Graph user is a valid PI.

    Args:
        user: GraphUser from Microsoft Graph, or None if lookup returned
              no result (user does not exist in the tenant).

    Returns:
        Ok() if the user exists and is enabled.
        Invalid(reason='not_found') if user is None.
        Invalid(reason='account_disabled') if account_enabled is False.
    """
    if user is None:
        return Invalid(reason="not_found")
    if not user.account_enabled:
        return Invalid(reason="account_disabled")
    return Ok()
