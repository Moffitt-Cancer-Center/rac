# pattern: Imperative Shell
"""Signal-triggered submission finalization.

Called from:
1. services/assets/upload.finalize_upload — after upload completes, checks if all assets ready
2. services/assets/external_fetch.fetch_external_asset — after fetch succeeds

NOT polled. If an asset completes but this function crashes, submission stays
in awaiting_scan. Operator triggers retry via POST /admin/submissions/{id}/force-finalize.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    ApprovalEvent,
    Asset,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.services.submissions.fsm import transition

logger = structlog.get_logger(__name__)

# Asset terminal statuses
_READY = "ready"
_FAILED_STATUSES = frozenset({"hash_mismatch", "unreachable"})
_PENDING_STATUSES = frozenset({"pending"})


async def finalize_submission(
    session: AsyncSession,
    submission_id: UUID,
    *,
    dispatch_fn: Callable[[dict[str, object]], Awaitable[None]] | None = None,
) -> SubmissionStatus:
    """Called after an asset state change. Checks asset states:

    - All assets 'ready' → dispatch pipeline, stay at 'awaiting_scan'
      (pipeline handles the transition to awaiting_research_review).
    - Any asset 'hash_mismatch' or 'unreachable' → transition to
      'needs_user_action' via FSM; insert approval_event
      kind='asset_resolution_required' with details of the blocking assets.
    - Some pending → no-op, return current status.

    Returns the new (or unchanged) submission status.
    """
    # Load submission
    result = await session.execute(
        select(Submission).where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        logger.warning(
            "finalize_submission_submission_not_found",
            submission_id=str(submission_id),
        )
        return SubmissionStatus.awaiting_scan

    # Only act on submissions still awaiting_scan
    if submission.status != SubmissionStatus.awaiting_scan:
        logger.debug(
            "finalize_submission_skipped_wrong_status",
            submission_id=str(submission_id),
            status=submission.status,
        )
        return SubmissionStatus(submission.status)

    # Load all assets for this submission
    assets_result = await session.execute(
        select(Asset).where(Asset.submission_id == submission_id)
    )
    assets = list(assets_result.scalars().all())

    # Categorise
    failed = [a for a in assets if a.status in _FAILED_STATUSES]
    pending = [a for a in assets if a.status in _PENDING_STATUSES]

    if failed:
        # Transition to needs_user_action via FSM; convert to models.SubmissionStatus
        fsm_new_status = transition(submission.status, "asset_failed")  # type: ignore[arg-type]
        new_status = SubmissionStatus(fsm_new_status.value)
        submission.status = new_status

        # Record an approval_event with details of blocking assets
        blocking_details = [
            {
                "asset_name": a.name,
                "asset_id": str(a.id),
                "status": a.status,
                "expected_sha256": a.expected_sha256,
                "actual_sha256": a.actual_sha256,
            }
            for a in failed
        ]
        event = ApprovalEvent(
            submission_id=submission_id,
            kind="asset_resolution_required",
            actor_principal_id=None,
            payload={"blocking_assets": blocking_details},
            decision=None,
            comment=(
                f"{len(failed)} asset(s) require resolution before the pipeline can proceed"
            ),
        )
        session.add(event)
        await session.flush()

        logger.info(
            "finalize_submission_needs_user_action",
            submission_id=str(submission_id),
            failed_count=len(failed),
        )
        return new_status

    if pending:
        # Still waiting on some assets
        logger.debug(
            "finalize_submission_pending_assets",
            submission_id=str(submission_id),
            pending_count=len(pending),
        )
        return SubmissionStatus(submission.status)

    # All assets ready (or no assets): dispatch pipeline.
    # Pass a minimal trigger dict — the caller's dispatch_fn is responsible for
    # enriching with secrets and full payload if needed (e.g. route layer wraps
    # build_dispatch_payload before calling). This keeps finalize_submission
    # dependency-free from Settings and lets tests inject simple mocks.
    if dispatch_fn is not None:
        try:
            trigger_payload: dict[str, object] = {
                "submission_id": str(submission_id),
                "trigger": "asset_finalize",
            }
            await dispatch_fn(trigger_payload)
            logger.info(
                "finalize_submission_dispatched",
                submission_id=str(submission_id),
            )
        except Exception as exc:
            logger.error(
                "finalize_submission_dispatch_failed",
                submission_id=str(submission_id),
                error=str(exc),
            )
            # Don't re-raise: submission stays in awaiting_scan for operator retry

    return SubmissionStatus(submission.status)
