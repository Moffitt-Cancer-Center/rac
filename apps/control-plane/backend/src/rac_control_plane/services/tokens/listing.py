# pattern: Imperative Shell
"""Token listing service.

Queries reviewer_token and left-joins revoked_token to include revocation status.
Returns plain dataclass DTOs (no ORM objects exposed to callers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import ReviewerToken, RevokedToken


@dataclass(frozen=True)
class TokenListRow:
    """DTO for a single row in the token listing."""
    jti: UUID
    reviewer_label: str | None
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    scope: str
    issued_by_principal_id: UUID | None


async def list_tokens_for_app(
    session: AsyncSession,
    *,
    app_id: UUID,
    include_revoked: bool = False,
) -> list[TokenListRow]:
    """List reviewer tokens for an app, optionally including revoked ones.

    Performs a LEFT JOIN reviewer_token → revoked_token on jti to determine
    revocation status.

    Args:
        session: Async SQLAlchemy session.
        app_id: UUID of the app whose tokens to list.
        include_revoked: If False (default), exclude revoked tokens.
                         If True, include all tokens; revoked_at will be set.

    Returns:
        List of TokenListRow DTOs ordered by created_at descending.
    """
    stmt = (
        select(ReviewerToken, RevokedToken)
        .outerjoin(RevokedToken, ReviewerToken.jti == RevokedToken.jti)
        .where(ReviewerToken.app_id == app_id)
        .order_by(ReviewerToken.created_at.desc())
    )

    rows = (await session.execute(stmt)).all()

    result: list[TokenListRow] = []
    for rt, revoked in rows:
        if not include_revoked and revoked is not None:
            continue
        result.append(
            TokenListRow(
                jti=UUID(rt.jti),
                reviewer_label=rt.reviewer_label,
                issued_at=rt.created_at,
                expires_at=rt.expires_at,
                revoked_at=revoked.revoked_at if revoked else None,
                scope=rt.scope,
                issued_by_principal_id=rt.issued_by_principal_id,
            )
        )
    return result


async def list_tokens_for_reviewer(
    session: AsyncSession,
    *,
    reviewer_label_pattern: str,
) -> list[TokenListRow]:
    """List reviewer tokens matching a label pattern (LIKE search).

    Admin-level view: returns tokens across all apps.

    Args:
        session: Async SQLAlchemy session.
        reviewer_label_pattern: SQL LIKE pattern, e.g. "Reviewer %".

    Returns:
        List of TokenListRow DTOs.
    """
    stmt = (
        select(ReviewerToken, RevokedToken)
        .outerjoin(RevokedToken, ReviewerToken.jti == RevokedToken.jti)
        .where(ReviewerToken.reviewer_label.like(reviewer_label_pattern))
        .order_by(ReviewerToken.created_at.desc())
    )

    rows = (await session.execute(stmt)).all()

    return [
        TokenListRow(
            jti=UUID(rt.jti),
            reviewer_label=rt.reviewer_label,
            issued_at=rt.created_at,
            expires_at=rt.expires_at,
            revoked_at=revoked.revoked_at if revoked else None,
            scope=rt.scope,
            issued_by_principal_id=rt.issued_by_principal_id,
        )
        for rt, revoked in rows
    ]
