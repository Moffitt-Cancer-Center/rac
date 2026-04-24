# pattern: Imperative Shell
"""Access log viewer API route.

GET /apps/{app_id}/access-log — paginated, filterable access log viewer.

Auth: app owner (PI or current submitter) OR admin.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.access_log import AccessLogItem, AccessLogListResponse
from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import App, Submission
from rac_control_plane.errors import ForbiddenError, NotFoundError
from rac_control_plane.services.access_log.query import list_access_log
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/apps", tags=["access-log"])


async def _get_app_or_404(session: AsyncSession, app_id: UUID) -> App:
    """Fetch App by id or raise NotFoundError."""
    result = await session.execute(select(App).where(App.id == app_id))
    app = result.scalar_one_or_none()
    if app is None:
        raise NotFoundError(public_message=f"App {app_id} not found.")
    return app


async def _get_submission_owner(session: AsyncSession, submission_id: UUID | None) -> UUID | None:
    """Return the submitter_principal_id of the given submission, or None."""
    if submission_id is None:
        return None
    result = await session.execute(
        select(Submission.submitter_principal_id).where(Submission.id == submission_id)
    )
    return result.scalar_one_or_none()


def _is_app_owner_or_admin(
    app: App,
    principal: Principal,
    submitter_principal_id: UUID | None,
    *,
    admin_role: str,
) -> bool:
    """Return True if principal is the PI, the submitter, or has the admin role."""
    if admin_role in principal.roles:
        return True
    if principal.oid == app.pi_principal_id:
        return True
    if submitter_principal_id is not None and principal.oid == submitter_principal_id:
        return True
    return False


@router.get("/{app_id}/access-log", response_model=AccessLogListResponse)
async def get_access_log(
    app_id: UUID,
    principal: Annotated[Principal, Depends(current_principal)],
    session: AsyncSession = Depends(get_session),
    before: UUID | None = Query(default=None, description="Cursor: return rows older than this id"),
    limit: int = Query(default=50, ge=1, le=500, description="Max rows per page (capped at 100)"),
    mode: str | None = Query(default=None, description="Filter by access_mode: token_required|public"),  # noqa: E501
    jti: UUID | None = Query(default=None, description="Filter by reviewer_token_jti"),
    status: int | None = Query(default=None, description="Filter by exact HTTP status code"),
) -> AccessLogListResponse:
    """Paginated, filterable access log for the given app.

    Uses keyset pagination on UUIDv7 ids (newest-first).
    ``before=<uuid>`` returns rows older than that id.
    ``next_cursor`` in the response is the last item's id (None if fewer than limit returned).

    Auth: app PI, current submitter, or admin.
    """
    settings = get_settings()
    app = await _get_app_or_404(session, app_id)
    submitter_oid = await _get_submission_owner(session, app.current_submission_id)

    if not _is_app_owner_or_admin(
        app, principal, submitter_oid, admin_role=settings.approver_role_it
    ):
        raise ForbiddenError(public_message="Only the app owner or admin may view the access log.")

    rows = await list_access_log(
        session,
        app_id=app_id,
        before=before,
        limit=limit,
        mode_filter=mode,
        jti_filter=str(jti) if jti is not None else None,
        status_filter=status,
    )

    items = [
        AccessLogItem(
            id=row.id,
            created_at=row.created_at,
            reviewer_token_jti=row.reviewer_token_jti,
            reviewer_label=row.reviewer_label,
            access_mode=row.access_mode,
            method=row.method,
            path=row.path,
            upstream_status=row.upstream_status,
            latency_ms=row.latency_ms,
            source_ip=row.source_ip,
        )
        for row in rows
    ]

    # next_cursor: the last item's id if we returned a full page, else None.
    next_cursor: UUID | None = None
    if len(rows) >= min(limit, 100):
        next_cursor = rows[-1].id

    return AccessLogListResponse(items=items, next_cursor=next_cursor)
