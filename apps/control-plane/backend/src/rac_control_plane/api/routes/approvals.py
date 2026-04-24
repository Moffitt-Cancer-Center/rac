# pattern: Imperative Shell
"""Approval API routes.

Endpoints:
- POST /submissions/{id}/approvals/research
- POST /submissions/{id}/approvals/it
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.approvals import ApprovalRequest
from rac_control_plane.api.schemas.submissions import SubmissionResponse
from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.submission_repo import get_by_id
from rac_control_plane.errors import ForbiddenError, NotFoundError
from rac_control_plane.services.approvals.record import record_approval
from rac_control_plane.services.approvals.role_check import principal_can_approve_stage
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/submissions", tags=["approvals"])


async def _do_approve(
    submission_id: str,
    stage: str,
    request: ApprovalRequest,
    principal: Principal,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> SubmissionResponse:
    """Shared handler logic for research and IT approval endpoints."""
    settings = get_settings()

    # Role check
    if not principal_can_approve_stage(principal, stage, settings=settings):  # type: ignore[arg-type]
        raise ForbiddenError(
            public_message=f"Principal lacks the '{stage}' approver role"
        )

    # Parse UUID
    try:
        sub_uuid = UUID(submission_id)
    except ValueError as exc:
        raise NotFoundError(public_message="Submission not found") from exc

    # Load submission
    submission = await get_by_id(session, sub_uuid)
    if submission is None:
        raise NotFoundError(public_message="Submission not found")

    # Record approval (may raise ConflictError if wrong state)
    updated = await record_approval(
        session,
        submission,
        principal,
        stage=stage,  # type: ignore[arg-type]
        decision=request.decision,
        notes=request.notes,
    )

    await session.commit()
    await session.refresh(updated)

    return SubmissionResponse(
        id=updated.id,
        slug=updated.slug,
        status=updated.status,
        submitter_principal_id=updated.submitter_principal_id,
        agent_id=updated.agent_id,
        github_repo_url=updated.github_repo_url,
        git_ref=updated.git_ref,
        dockerfile_path=updated.dockerfile_path,
        pi_principal_id=updated.pi_principal_id,
        dept_fallback=updated.dept_fallback,
        manifest=updated.manifest,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@router.post("/{submission_id}/approvals/research", response_model=SubmissionResponse)
async def approve_research(
    submission_id: str,
    request: ApprovalRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> SubmissionResponse:
    """Research stage approval endpoint.

    Verifies:
    - AC2.2: Research approver transitions submission to awaiting_it_review.
    - AC10.2: Approval duration metric emitted.

    Args:
        submission_id: UUID of the submission.
        request: Approval decision with optional notes.
        principal: Current authenticated principal.
        background_tasks: FastAPI BackgroundTasks.
        session: Database session.

    Returns:
        Updated submission.

    Raises:
        403: Principal lacks research approver role.
        404: Submission not found.
        409: Submission not in awaiting_research_review state.
    """
    return await _do_approve(
        submission_id, "research", request, principal, background_tasks, session
    )


@router.post("/{submission_id}/approvals/it", response_model=SubmissionResponse)
async def approve_it(
    submission_id: str,
    request: ApprovalRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> SubmissionResponse:
    """IT stage approval endpoint.

    On 'approve' decision, enqueues provisioning via background task stub
    (Task 6 will wire the real provisioning orchestrator).

    Verifies:
    - AC2.2: IT approver transitions submission to approved (then provisioning).
    - AC10.2: Approval duration metric emitted.

    Args:
        submission_id: UUID of the submission.
        request: Approval decision with optional notes.
        principal: Current authenticated principal.
        background_tasks: FastAPI BackgroundTasks.
        session: Database session.

    Returns:
        Updated submission.

    Raises:
        403: Principal lacks IT approver role.
        404: Submission not found.
        409: Submission not in awaiting_it_review state.
    """
    return await _do_approve(
        submission_id, "it", request, principal, background_tasks, session
    )
