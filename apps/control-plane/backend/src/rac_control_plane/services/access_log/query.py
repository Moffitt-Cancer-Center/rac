# pattern: Imperative Shell
"""Access log paginated query service.

Keyset pagination: UUIDv7 ids are time-ordered, so `before=<cursor>` filters
to rows with id < cursor (older). Results are returned newest-first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import AccessLog, ReviewerToken

_MAX_LIMIT = 100


@dataclass(frozen=True)
class AccessLogRow:
    id: UUID
    created_at: datetime
    reviewer_token_jti: str | None
    reviewer_label: str | None  # from JOIN on reviewer_token
    access_mode: str | None
    method: str | None
    path: str | None
    upstream_status: int | None
    latency_ms: int | None
    source_ip: str | None


async def list_access_log(
    session: AsyncSession,
    *,
    app_id: UUID,
    before: UUID | None = None,
    limit: int = 50,
    mode_filter: str | None = None,  # 'token_required' | 'public'
    jti_filter: str | None = None,
    status_filter: int | None = None,  # exact HTTP status code
) -> list[AccessLogRow]:
    """Return access_log entries for a given app, newest-first.

    Keyset pagination: ``before=<cursor>`` returns rows with id < cursor.
    Limit is capped at 100 regardless of the requested value.
    JOINs reviewer_token to pull reviewer_label.
    """
    effective_limit = min(limit, _MAX_LIMIT)

    stmt = (
        select(
            AccessLog.id,
            AccessLog.created_at,
            AccessLog.reviewer_token_jti,
            AccessLog.access_mode,
            AccessLog.method,
            # path stored in the legacy 'action' column for Phase-2 rows;
            # the shim stores the actual HTTP path in access_log — use the
            # action column as fallback.
            AccessLog.action.label("path"),
            AccessLog.upstream_status,
            AccessLog.latency_ms,
            AccessLog.source_ip,
            ReviewerToken.reviewer_label,
        )
        .outerjoin(
            ReviewerToken,
            AccessLog.reviewer_token_jti == ReviewerToken.jti,
        )
        .where(AccessLog.app_id == app_id)
        .order_by(AccessLog.id.desc())
        .limit(effective_limit)
    )

    if before is not None:
        # UUIDv7 lexicographic ordering: id < before means "older than before"
        stmt = stmt.where(
            text("access_log.id < :before").bindparams(before=before)
        )

    if mode_filter is not None:
        stmt = stmt.where(AccessLog.access_mode == mode_filter)

    if jti_filter is not None:
        stmt = stmt.where(AccessLog.reviewer_token_jti == str(jti_filter))

    if status_filter is not None:
        stmt = stmt.where(AccessLog.upstream_status == status_filter)

    result = await session.execute(stmt)
    rows = result.all()

    return [
        AccessLogRow(
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
