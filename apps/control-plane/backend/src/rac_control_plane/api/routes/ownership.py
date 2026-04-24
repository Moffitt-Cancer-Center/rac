# pattern: Imperative Shell
"""Ownership management API routes.

Endpoints:
  POST /admin/apps/{app_id}/ownership/transfer
  GET  /admin/ownership/flags

Admin-only: transfers the PI ownership of an app to a new Entra principal.
Preserves the full approval event audit trail (AC9.3).

Verifies: rac-v1.AC9.2, rac-v1.AC9.3
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.ownership import (
    OwnershipFlagListResponse,
    OwnershipFlagResponse,
    OwnershipTransferRequest,
)
from rac_control_plane.auth.dependencies import require_admin
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import App, AppOwnershipFlag, AppOwnershipFlagReview
from rac_control_plane.errors import NotFoundError
from rac_control_plane.services.ownership.transfer import TransferRequest, transfer_ownership

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["ownership"])


class AppResponse(BaseModel):
    """Response schema for an App after ownership transfer."""

    id: UUID
    slug: str
    pi_principal_id: UUID
    dept_fallback: str
    current_submission_id: UUID | None = None
    target_port: int
    access_mode: str

    model_config = {"from_attributes": True}


@router.get("/ownership/flags", response_model=OwnershipFlagListResponse)
async def list_ownership_flags(
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> list[OwnershipFlagResponse]:
    """List open ownership flags (flags with no corresponding review row).

    An open flag is one where no AppOwnershipFlagReview row exists for the flag.
    Returns flags enriched with app_slug and pi_display_name (best-effort from Graph).

    Admin-only.

    Verifies: rac-v1.AC9.2
    """
    # LEFT JOIN flags → reviews; filter WHERE review IS NULL
    result = await session.execute(
        select(AppOwnershipFlag, App.slug)
        .join(App, App.id == AppOwnershipFlag.app_id)
        .outerjoin(
            AppOwnershipFlagReview,
            AppOwnershipFlagReview.flag_id == AppOwnershipFlag.id,
        )
        .where(AppOwnershipFlagReview.id.is_(None))
        .order_by(AppOwnershipFlag.flagged_at.asc())
    )
    rows = result.all()

    # Best-effort PI display name lookup from Graph gateway
    responses: list[OwnershipFlagResponse] = []
    for flag, app_slug in rows:
        pi_display_name: str | None = None
        try:
            from rac_control_plane.services.ownership.graph_gateway import get_user  # noqa: PLC0415
            graph_user = await get_user(flag.pi_principal_id)
            if graph_user is not None:
                pi_display_name = graph_user.display_name
        except Exception:  # noqa: BLE001
            logger.warning(
                "graph_lookup_failed_for_flag",
                flag_id=str(flag.id),
                pi_principal_id=str(flag.pi_principal_id),
            )

        responses.append(
            OwnershipFlagResponse(
                flag_id=flag.id,
                app_id=flag.app_id,
                app_slug=app_slug,
                pi_principal_id=flag.pi_principal_id,
                pi_display_name=pi_display_name,
                reason=flag.reason,
                flagged_at=flag.flagged_at,
            )
        )

    return responses


@router.post("/apps/{app_id}/ownership/transfer", response_model=AppResponse)
async def transfer_app_ownership(
    app_id: str,
    body: OwnershipTransferRequest,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Transfer PI ownership of an app to a new Entra principal.

    Admin-only.  Validates the new PI via Microsoft Graph before committing.
    Resolves any open 'account_disabled' flags on the app.  Existing
    approval_event rows are not modified (AC9.3).

    Args:
        app_id: UUID of the app to transfer.
        body: Transfer parameters (new_pi_principal_id, new_dept_fallback, justification).
        principal: Current admin principal (via require_admin dependency).
        session: Database session.

    Returns:
        Updated App record.

    Raises:
        403: Not an admin.
        404: App not found.
        422: New PI is invalid in Graph.
    """
    try:
        app_uuid = UUID(app_id)
    except ValueError as exc:
        raise NotFoundError(public_message="App not found") from exc

    req = TransferRequest(
        app_id=app_uuid,
        new_pi_principal_id=body.new_pi_principal_id,
        new_dept_fallback=body.new_dept_fallback,
        justification=body.justification,
    )

    updated_app = await transfer_ownership(
        session,
        req,
        actor_principal_id=principal.oid,
    )

    await session.commit()
    await session.refresh(updated_app)

    return updated_app
