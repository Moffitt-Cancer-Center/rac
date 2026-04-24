# pattern: Imperative Shell
"""Approval recording service.

Applies the FSM transition, persists approval_event, emits the
approval-duration metric, and (for IT approvals) enqueues provisioning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import ApprovalEvent, Submission, SubmissionStatus
from rac_control_plane.errors import ConflictError
from rac_control_plane.metrics import approval_duration_histogram
from rac_control_plane.services.provisioning.orchestrator import provision_submission
from rac_control_plane.services.submissions.fsm import (
    InvalidTransitionError,
    transition,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Stage-to-expected-state mapping
# ---------------------------------------------------------------------------

_STAGE_REQUIRED_STATE: dict[str, SubmissionStatus] = {
    "research": SubmissionStatus.awaiting_research_review,
    "it": SubmissionStatus.awaiting_it_review,
}

# ---------------------------------------------------------------------------
# Decision + stage → FSM event mapping
# ---------------------------------------------------------------------------

_DECISION_EVENT: dict[tuple[str, str], str] = {
    ("approve", "research"): "research_approved",
    ("reject", "research"): "research_rejected",
    ("request_changes", "research"): "request_changes",
    ("approve", "it"): "it_approved",
    ("reject", "it"): "it_rejected",
    ("request_changes", "it"): "request_changes",
}


# ---------------------------------------------------------------------------
# Provisioning background task wrapper (Task 6 wired)
# ---------------------------------------------------------------------------

async def _run_provisioning_background(
    submission_id: UUID,
    session: AsyncSession,
) -> None:
    """Background task that runs the real provisioning orchestrator."""
    from sqlalchemy import select

    from rac_control_plane.data.models import Submission as SubmissionModel

    try:
        result = await session.execute(
            select(SubmissionModel).where(SubmissionModel.id == submission_id)
        )
        submission = result.scalar_one_or_none()
        if submission is None:
            logger.warning("provisioning_submission_not_found", submission_id=str(submission_id))
            return
        await provision_submission(session, submission)
    except Exception:
        logger.exception("provisioning_background_error", submission_id=str(submission_id))


# ---------------------------------------------------------------------------
# Main record function
# ---------------------------------------------------------------------------

async def record_approval(
    session: AsyncSession,
    submission: Submission,
    principal: Principal,
    stage: Literal["research", "it"],
    decision: Literal["approve", "reject", "request_changes"],
    notes: str | None,
) -> Submission:
    """Apply an approval decision to a submission.

    Steps:
    1. Verify submission is in the correct state for this stage.
    2. Map decision + stage to FSM event.
    3. Apply FSM transition.
    4. Update submission.status + submission.updated_at.
    5. Insert approval_event row.
    6. Emit approval-duration metric.
    7. If IT-approved, enqueue provisioning via background stub.

    Args:
        session: Async SQLAlchemy session (caller commits).
        submission: Submission ORM object (loaded by the route).
        principal: Approver's principal.
        stage: Approval stage ('research' or 'it').
        decision: Decision value ('approve', 'reject', 'request_changes').
        notes: Optional reviewer notes.

    Returns:
        Updated Submission object.

    Raises:
        ConflictError: Submission is not in the expected state for this stage.
        InvalidTransitionError: FSM transition not valid (caught, re-raised as ConflictError).
    """
    # Step 1: verify state
    required_state = _STAGE_REQUIRED_STATE[stage]
    if submission.status != required_state:
        raise ConflictError(
            public_message=(
                f"Submission is in state '{submission.status}', not"
                f" '{required_state}' — invalid state for {stage} approval"
            )
        )

    # Step 2: map to event
    event = _DECISION_EVENT[(decision, stage)]

    # Step 3: apply FSM transition (may raise InvalidTransitionError)
    try:
        new_status = transition(submission.status, event)  # type: ignore[arg-type]
    except InvalidTransitionError as exc:
        raise ConflictError(
            public_message=f"invalid_state_for_stage: {exc.public_message}"
        ) from exc

    # Step 4: update submission
    submission.status = new_status  # type: ignore[assignment]
    submission.updated_at = datetime.now(UTC)
    session.add(submission)
    await session.flush()

    # Step 5: persist approval_event
    approval_event = ApprovalEvent(
        submission_id=submission.id,
        kind=f"{stage}_decision",
        actor_principal_id=principal.oid,
        decision=decision,
        comment=notes,
    )
    session.add(approval_event)
    await session.flush()

    # Step 6: emit approval-duration metric (approve/reject only — not request_changes)
    if decision in ("approve", "reject"):
        now = datetime.now(UTC)
        # created_at may be naive if the DB returned it without tz info
        created = submission.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        elapsed = (now - created).total_seconds()
        approval_duration_histogram.record(
            elapsed,
            {"decision": decision, "stage": stage},
        )

    # Step 7: enqueue provisioning on IT approval (Task 6: real orchestrator)
    if event == "it_approved":
        await _run_provisioning_background(submission.id, session)

    return submission
