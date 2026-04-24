# pattern: Imperative Shell
"""Token revocation service.

Writes to the append-only revoked_token table. No UPDATE or DELETE paths.
Authorization must be enforced by the calling route layer.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import ApprovalEvent, ReviewerToken, RevokedToken
from rac_control_plane.errors import NotFoundError

logger = structlog.get_logger(__name__)


async def revoke_token(
    session: AsyncSession,
    *,
    jti: UUID,
    actor_principal_id: UUID,
    reason: str | None,
) -> None:
    """Revoke a reviewer token by its jti.

    Steps:
    1. Look up reviewer_token by jti (NotFoundError if missing).
    2. INSERT into revoked_token with expires_at = reviewer_token.expires_at.
    3. INSERT approval_event(kind='reviewer_token_revoked').

    Authorization is the caller's responsibility (checked in the route layer).

    Args:
        session: Async SQLAlchemy session (caller commits).
        jti: UUID of the token to revoke.
        actor_principal_id: OID of the principal performing the revocation.
        reason: Optional human-readable reason for revocation.

    Raises:
        NotFoundError: No reviewer_token row exists for this jti.
    """
    # 1. Look up the token
    stmt = select(ReviewerToken).where(ReviewerToken.jti == str(jti))
    result = await session.execute(stmt)
    token_row = result.scalar_one_or_none()
    if token_row is None:
        raise NotFoundError(public_message=f"Reviewer token {jti} not found.")

    # 2. Idempotency guard: if already revoked, return silently. A caller that
    # retries a DELETE after a network timeout must not receive a 500 from
    # the UNIQUE constraint on revoked_token.jti. AC7.2 cares about "revoked
    # within 60s" — being revoked twice is equivalent to being revoked once.
    existing_stmt = select(RevokedToken).where(RevokedToken.jti == str(jti))
    existing_result = await session.execute(existing_stmt)
    if existing_result.scalar_one_or_none() is not None:
        return

    # 3. INSERT revoked_token (append-only)
    revoked = RevokedToken(
        jti=str(jti),
        revoked_by_principal_id=actor_principal_id,
        reason=reason,
        expires_at=token_row.expires_at,
    )
    session.add(revoked)
    await session.flush()

    # 3. INSERT approval_event
    event = ApprovalEvent(
        submission_id=None,
        kind="reviewer_token_revoked",
        actor_principal_id=actor_principal_id,
        payload={
            "jti": str(jti),
            "reason": reason,
            "reviewer_label": token_row.reviewer_label,
        },
    )
    session.add(event)
    await session.flush()

    logger.info(
        "reviewer_token_revoked",
        jti=str(jti),
        actor=str(actor_principal_id),
        reason=reason,
    )
