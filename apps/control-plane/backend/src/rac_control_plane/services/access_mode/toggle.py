# pattern: Imperative Shell
"""Access mode toggle service.

Loads app + submission, validates via pure functions, updates app.access_mode,
inserts an approval_event. This is the one location where app.access_mode is
mutated — app is not append-only by design.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import AccessMode, App, ApprovalEvent, Submission
from rac_control_plane.errors import ForbiddenError, NotFoundError, ValidationApiError
from rac_control_plane.services.access_mode.validation import (
    Invalid,
    Ok,
    can_set_public_with_status,
    can_set_token_required,
)
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


async def set_access_mode(
    session: AsyncSession,
    *,
    app_id: UUID,
    new_mode: Literal["token_required", "public"],
    actor_principal_id: UUID,
    actor_roles: frozenset[str],
    notes: str,
) -> App:
    """Toggle app.access_mode between 'token_required' and 'public'.

    Steps:
    1. Load app (404 if not found).
    2. Load current submission to get submitter_principal_id and status.
    3. Validate via pure functions.
    4. UPDATE app.access_mode.
    5. INSERT approval_event.

    Args:
        session: Async SQLAlchemy session (caller commits).
        app_id: App to update.
        new_mode: Target mode ("public" or "token_required").
        actor_principal_id: OID of the principal making the change.
        actor_roles: Roles of the actor principal (for admin check).
        notes: Required rationale for the change (min 10 chars enforced by route).

    Returns:
        Updated App ORM object.

    Raises:
        NotFoundError: App not found.
        ValidationApiError: App is not deployed (for public mode).
        ForbiddenError: Principal not authorized.
    """
    from rac_control_plane.auth.principal import Principal

    settings = get_settings()
    admin_role = settings.approver_role_it

    # 1. Load app
    app_result = await session.execute(select(App).where(App.id == app_id))
    app = app_result.scalar_one_or_none()
    if app is None:
        raise NotFoundError(public_message=f"App {app_id} not found.")

    # 2. Load current submission (for owner check + deployed status)
    submitter_principal_id: UUID | None = None
    submission_status = None
    if app.current_submission_id is not None:
        sub_result = await session.execute(
            select(Submission).where(Submission.id == app.current_submission_id)
        )
        sub = sub_result.scalar_one_or_none()
        if sub is not None:
            submitter_principal_id = sub.submitter_principal_id
            submission_status = sub.status

    # 3. Build a minimal Principal for validation
    principal = Principal(
        oid=actor_principal_id,
        kind="user",
        roles=actor_roles,
    )

    if new_mode == "public":
        result = can_set_public_with_status(
            app,
            principal,
            submitter_principal_id,
            submission_status=submission_status,
            admin_role=admin_role,
            require_publication=settings.require_publication_for_public,
        )
    else:
        result = can_set_token_required(
            app,
            principal,
            submitter_principal_id,
            admin_role=admin_role,
        )

    if isinstance(result, Invalid):
        if result.reason == "not_authorized":
            raise ForbiddenError(
                public_message="Only the app owner or admin may change the access mode."
            )
        if result.reason == "not_deployed":
            raise ValidationApiError(
                code="not_deployed",
                public_message="App must be in 'deployed' state to enable public access.",
            )
        if result.reason == "publication_required":
            raise ValidationApiError(
                code="publication_required",
                public_message="A publication DOI is required before enabling public access.",
            )

    # 4. UPDATE app.access_mode
    old_mode = app.access_mode
    app.access_mode = AccessMode.public if new_mode == "public" else AccessMode.token_required
    session.add(app)
    await session.flush()

    # 5. INSERT approval_event
    event = ApprovalEvent(
        submission_id=app.current_submission_id,
        kind="access_mode_changed",
        actor_principal_id=actor_principal_id,
        payload={
            "from": str(old_mode),
            "to": new_mode,
            "notes": notes,
        },
    )
    session.add(event)
    await session.flush()

    logger.info(
        "access_mode_changed",
        app_id=str(app_id),
        old_mode=str(old_mode),
        new_mode=new_mode,
        actor=str(actor_principal_id),
    )

    return app
