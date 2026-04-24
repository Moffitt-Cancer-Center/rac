# pattern: Functional Core
"""Pure approval role-check logic.

No I/O; no side effects.  Composes with Principal and Settings to determine
whether a given principal may approve a given stage.
"""

from typing import Literal

from rac_control_plane.auth.principal import Principal
from rac_control_plane.settings import Settings


def principal_can_approve_stage(
    principal: Principal,
    stage: Literal["research", "it"],
    *,
    settings: Settings,
) -> bool:
    """Return True if ``principal`` holds the approver role for ``stage``.

    Args:
        principal: Authenticated principal (user with roles populated from JWT).
        stage: Approval stage — ``'research'`` or ``'it'``.
        settings: Application settings (provides role name mappings).

    Returns:
        True iff the principal's roles include the required approver role.
    """
    role_name = (
        settings.approver_role_research
        if stage == "research"
        else settings.approver_role_it
    )
    return role_name in principal.roles
