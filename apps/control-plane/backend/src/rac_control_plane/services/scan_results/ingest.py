# pattern: Imperative Shell
"""Ingest a pipeline callback into the database.

Wraps all the side-effectful steps:
1. Insert ScanResult row.
2. Advance submission FSM.
3. Insert ApprovalEvent (kind="scan_completed").
4. Emit metric.
5. Enqueue outbound webhook deliveries.
6. Purge the single-use callback secret.
7. Commit the session.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.webhooks import PipelineCallback
from rac_control_plane.data.models import ApprovalEvent, ScanResult, Submission
from rac_control_plane.services.submissions.fsm import (
    SubmissionStatus,
    TransitionEvent,
    transition,
)

logger = structlog.get_logger(__name__)

# Verdict → FSM event mapping
_VERDICT_TO_EVENT = {
    "passed": "scan_passed",
    "partial_passed": "scan_passed",
    "rejected": "severity_gate_failed",
    "partial_rejected": "severity_gate_failed",
    "build_failed": "pipeline_error",
}


async def ingest(
    session: AsyncSession,
    submission: Submission,
    callback: PipelineCallback,
    *,
    metric_emitter: Callable[[str], None] | None = None,
    deliver_events: Callable[..., Awaitable[None]] | None = None,
    kv_purge: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Ingest a pipeline callback and advance the submission state machine.

    Args:
        session:       Async DB session to write into.
        submission:    ORM Submission row (loaded by the route handler).
        callback:      Parsed PipelineCallback payload.
        metric_emitter: Optional ``lambda verdict: counter.add(1, {"verdict": verdict})``.
        deliver_events: Optional coroutine factory for outbound webhook delivery.
        kv_purge:      Optional coroutine to purge the single-use KV secret.

    Raises:
        InvalidTransitionError: If the submission is in an unexpected state
            (wrapped to a 409 by the route handler).
    """
    now = datetime.now(tz=UTC)

    # 1. Insert ScanResult row
    scan_result = ScanResult(
        submission_id=submission.id,
        verdict=callback.verdict,
        effective_severity=callback.effective_severity,
        findings=callback.findings,
        build_log_uri=callback.build_log_uri,
        sbom_uri=callback.sbom_uri,
        grype_report_uri=callback.grype_report_uri,
        defender_report_uri=callback.defender_report_uri,
        image_digest=callback.image_digest,
        image_ref=callback.image_ref,
        defender_timed_out=callback.defender_timed_out,
    )
    session.add(scan_result)

    # 2. Advance FSM — raises InvalidTransitionError on bad state
    fsm_event = cast(TransitionEvent, _VERDICT_TO_EVENT[callback.verdict])
    new_status = transition(SubmissionStatus(submission.status), fsm_event)

    # 3. Update submission
    submission.status = new_status  # type: ignore[assignment]
    submission.updated_at = now

    # 4. Insert ApprovalEvent
    approval_event = ApprovalEvent(
        submission_id=submission.id,
        kind="scan_completed",
        actor_principal_id=None,  # system event
        payload={
            "verdict": callback.verdict,
            "effective_severity": callback.effective_severity,
        },
    )
    session.add(approval_event)

    # 5. Emit metric
    if metric_emitter is not None:
        metric_emitter(callback.verdict)

    # 6. Enqueue outbound webhook deliveries
    if deliver_events is not None:
        event_body = {
            "submission_id": str(submission.id),
            "verdict": callback.verdict,
            "effective_severity": callback.effective_severity,
            "new_status": new_status,
        }
        await deliver_events(
            session,
            "submission.scan_completed",
            submission.id,
            event_body,
        )

    # 7. Purge callback secret (single-use)
    if kv_purge is not None:
        secret_name = f"rac-pipeline-cb-{submission.id}"
        await kv_purge(secret_name)

    # 8. Commit
    await session.commit()

    logger.info(
        "scan_result_ingested",
        submission_id=str(submission.id),
        verdict=callback.verdict,
        new_status=new_status,
    )
