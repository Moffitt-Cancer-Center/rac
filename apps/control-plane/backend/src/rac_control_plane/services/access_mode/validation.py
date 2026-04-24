# pattern: Functional Core
"""Pure access mode validation.

No I/O, no side effects. Determines whether a principal may toggle app.access_mode
without touching the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import App, SubmissionStatus


@dataclass(frozen=True)
class Ok:
    """Validation passed — the toggle is allowed."""


@dataclass(frozen=True)
class Invalid:
    """Validation failed — the toggle is not allowed."""
    reason: Literal["not_deployed", "not_authorized", "publication_required"]


ValidationResult = Ok | Invalid


def _is_owner_or_admin(
    app: App,
    principal: Principal,
    submitter_principal_id: UUID | None,
    *,
    admin_role: str,
) -> bool:
    """Return True if principal is the PI, the current submitter, or has admin role."""
    if admin_role in principal.roles:
        return True
    if principal.oid == app.pi_principal_id:
        return True
    if submitter_principal_id is not None and principal.oid == submitter_principal_id:
        return True
    return False


def can_set_public_with_status(
    app: App,
    principal: Principal,
    submitter_principal_id: UUID | None = None,
    *,
    submission_status: SubmissionStatus | None,
    admin_role: str = "it_approver",
    require_publication: bool = False,
) -> ValidationResult:
    """Full validation including deployment-state check.

    This is the production entry point. can_set_public() above is kept for
    property testing (status-independent).

    Args:
        app: The App ORM object.
        principal: Authenticated principal.
        submitter_principal_id: submitter_principal_id from current submission.
        submission_status: Status of the current submission (None if no submission).
        admin_role: Admin role name.
        require_publication: Gate on publication DOI (reserved; False in v1).

    Returns:
        Ok() or Invalid(reason).
    """
    if not _is_owner_or_admin(app, principal, submitter_principal_id, admin_role=admin_role):
        return Invalid(reason="not_authorized")

    if submission_status != SubmissionStatus.deployed:
        return Invalid(reason="not_deployed")

    if require_publication:
        # Future: check submission.publication_doi is not None.
        # Not enforced in v1 (no such column yet).
        pass

    return Ok()


def can_set_token_required(
    app: App,
    principal: Principal,
    submitter_principal_id: UUID | None = None,
    *,
    admin_role: str = "it_approver",
) -> ValidationResult:
    """Determine whether principal may set app.access_mode = 'token_required'.

    Any owner or admin may flip back to token_required with no additional constraints.

    Args:
        app: The App ORM object.
        principal: Authenticated principal.
        submitter_principal_id: submitter_principal_id from current submission.
        admin_role: Admin role name.

    Returns:
        Ok() or Invalid(reason="not_authorized").
    """
    if not _is_owner_or_admin(app, principal, submitter_principal_id, admin_role=admin_role):
        return Invalid(reason="not_authorized")
    return Ok()
