# pattern: Imperative Shell
"""Provisioning admin API routes.

Endpoints:
- POST /admin/submissions/{id}/provisioning/retry
- GET  /admin/submissions/failed-provisions
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.auth.dependencies import require_admin
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import SubmissionStatus
from rac_control_plane.data.submission_repo import get_by_id
from rac_control_plane.errors import ConflictError, NotFoundError
from rac_control_plane.services.provisioning.orchestrator import provision_submission

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["provisioning"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class FailedProvisionRow(BaseModel):
    """A submission that failed provisioning and is awaiting retry."""

    submission_id: UUID
    slug: str
    pi_principal_id: UUID
    last_failure_reason: str
    failed_at: str
    retry_count: int


class RetryOutcomeResponse(BaseModel):
    """Response from a provisioning retry call."""

    submission_id: UUID
    success: bool
    error_code: str | None = None
    error_detail: str | None = None


# ---------------------------------------------------------------------------
# GET /admin/submissions/failed-provisions
# ---------------------------------------------------------------------------


@router.get("/submissions/failed-provisions", response_model=list[FailedProvisionRow])
async def list_failed_provisions(
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> list[FailedProvisionRow]:
    """List submissions in 'approved' state that have a provisioning_failed event
    but no subsequent provisioning_completed event.

    Auth: admin role required.
    """
    # Raw SQL: for each submission in approved state, find the most recent
    # provisioning_failed event that has no later provisioning_completed event.
    stmt = text("""
        SELECT
            s.id           AS submission_id,
            s.slug,
            s.pi_principal_id,
            ae.comment     AS last_failure_reason,
            ae.created_at  AS failed_at,
            (
                SELECT COUNT(*)
                FROM approval_event ae4
                WHERE ae4.submission_id = s.id
                  AND ae4.kind = 'provisioning_failed'
            ) AS retry_count
        FROM submission s
        JOIN approval_event ae ON ae.submission_id = s.id
            AND ae.kind = 'provisioning_failed'
        WHERE s.status = 'approved'
          AND NOT EXISTS (
            SELECT 1 FROM approval_event ae2
            WHERE ae2.submission_id = s.id
              AND ae2.kind = 'provisioning_completed'
              AND ae2.created_at > ae.created_at
          )
          AND ae.created_at = (
            SELECT MAX(ae3.created_at)
            FROM approval_event ae3
            WHERE ae3.submission_id = s.id
              AND ae3.kind = 'provisioning_failed'
          )
        ORDER BY ae.created_at DESC
    """)

    result = await session.execute(stmt)
    rows = result.fetchall()

    return [
        FailedProvisionRow(
            submission_id=row.submission_id,
            slug=row.slug,
            pi_principal_id=row.pi_principal_id,
            last_failure_reason=row.last_failure_reason or "",
            failed_at=str(row.failed_at),
            retry_count=int(row.retry_count),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# POST /admin/submissions/{id}/provisioning/retry
# ---------------------------------------------------------------------------


@router.post(
    "/submissions/{submission_id}/provisioning/retry",
    response_model=RetryOutcomeResponse,
)
async def retry_provisioning(
    submission_id: str,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> RetryOutcomeResponse:
    """Re-run provisioning for a failed or stuck submission.

    Auth: admin role required.
    Only allowed when submission is in 'approved' state (not yet deployed).

    Returns:
        RetryOutcomeResponse with success flag and optional error info.

    Raises:
        404: Submission not found.
        409: Submission not in 'approved' state.
        403: Principal lacks admin role.
    """
    try:
        sub_uuid = UUID(submission_id)
    except ValueError as exc:
        raise NotFoundError(public_message="Submission not found") from exc

    submission = await get_by_id(session, sub_uuid)
    if submission is None:
        raise NotFoundError(public_message="Submission not found")

    if submission.status != SubmissionStatus.approved:
        raise ConflictError(
            public_message=(
                f"Cannot retry provisioning: submission is in state "
                f"'{submission.status}', expected 'approved'"
            )
        )

    logger.info(
        "provisioning_retry_requested",
        submission_id=str(sub_uuid),
        admin_oid=str(principal.oid),
    )

    outcome = await provision_submission(session, submission)

    return RetryOutcomeResponse(
        submission_id=outcome.submission_id,
        success=outcome.success,
        error_code=outcome.error.code if outcome.error else None,
        error_detail=outcome.error.detail if outcome.error else None,
    )
