# pattern: Imperative Shell
"""Access mode toggle API route.

POST /apps/{app_id}/access-mode — flip between 'public' and 'token_required'
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.services.access_mode.toggle import set_access_mode

router = APIRouter(prefix="/apps", tags=["access-mode"])


class AccessModeRequest(BaseModel):
    """Body for POST /apps/{app_id}/access-mode."""
    mode: Literal["public", "token_required"]
    notes: str = Field(
        ...,
        min_length=10,
        description="Required rationale for the access mode change (min 10 chars).",
    )


class AccessModeResponse(BaseModel):
    """Response for POST /apps/{app_id}/access-mode."""
    app_id: UUID
    access_mode: str
    slug: str


@router.post("/{app_id}/access-mode", response_model=AccessModeResponse)
async def post_access_mode(
    app_id: UUID,
    body: AccessModeRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    session: AsyncSession = Depends(get_session),
) -> AccessModeResponse:
    """Toggle the access mode for the given app.

    Auth: app PI, current submitter, or admin.

    Raises:
        404: App not found.
        403: Not authorized.
        422: App not deployed (for 'public' mode), or notes too short.
    """
    app = await set_access_mode(
        session,
        app_id=app_id,
        new_mode=body.mode,
        actor_principal_id=principal.oid,
        actor_roles=principal.roles,
        notes=body.notes,
    )
    await session.commit()

    return AccessModeResponse(
        app_id=app.id,
        access_mode=str(app.access_mode),
        slug=app.slug,
    )
