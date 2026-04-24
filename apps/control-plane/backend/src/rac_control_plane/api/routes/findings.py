# pattern: Imperative Shell
"""Findings API — list detection findings and record researcher decisions.

Endpoints:
  GET  /submissions/{id}/findings
       Returns all findings for a submission with their latest decision.
       Auth: submitter, or any approver role, or admin.

  POST /submissions/{id}/findings/{finding_id}/decisions
       Record a decision on a finding.
       Body: { decision: accept|override|auto_fix|dismiss, notes?: str }
       Auth: submitter or admin only.
       After the last error finding is resolved, transitions submission back
       to awaiting_scan and emits a detection_resolved approval_event.
"""

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.findings import (
    DecisionRequest,
    DecisionResponse,
    FindingResponse,
)
from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data import detection_finding_store
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import ApprovalEvent, Submission, SubmissionStatus
from rac_control_plane.data.submission_repo import get_by_id
from rac_control_plane.errors import ForbiddenError
from rac_control_plane.services.detection.resolution import needs_user_action_resolved
from rac_control_plane.services.submissions.fsm import (
    SubmissionStatus as FsmStatus,
)
from rac_control_plane.services.submissions.fsm import transition
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/submissions", tags=["findings"])


def _make_redispatch_fn(settings_snapshot: Any) -> Any:
    """Build a dispatch callable for re-triggering the pipeline post-detection_resolved.

    Returns an async callable (payload) → None, or None if dispatch is not configured.
    """
    from rac_control_plane.services.pipeline_dispatch import github as gh_dispatch

    auth_token: str | None = None
    if settings_snapshot.gh_pat:
        auth_token = settings_snapshot.gh_pat.get_secret_value()

    if not auth_token:
        logger.warning("pipeline_redispatch_skipped_no_auth_token")
        return None

    owner = settings_snapshot.gh_pipeline_owner
    repo = settings_snapshot.gh_pipeline_repo

    async def _do_dispatch(payload: dict[str, Any]) -> None:
        await gh_dispatch.dispatch(owner, repo, payload, auth_token=auth_token)

    return _do_dispatch


def _can_view(principal: Principal, submission: Submission) -> bool:
    """Return True if principal can view findings for this submission."""
    settings = get_settings()
    return (
        principal.oid == submission.submitter_principal_id
        or settings.approver_role_research in principal.roles
        or settings.approver_role_it in principal.roles
    )


def _can_decide(principal: Principal, submission: Submission) -> bool:
    """Return True if principal can record decisions on findings."""
    settings = get_settings()
    return (
        principal.oid == submission.submitter_principal_id
        or settings.approver_role_it in principal.roles
    )


@router.get("/{submission_id}/findings", response_model=list[FindingResponse])
async def list_findings(
    submission_id: UUID,
    principal: Annotated[Principal, Depends(current_principal)],
    session: AsyncSession = Depends(get_session),
) -> list[FindingResponse]:
    """List all detection findings for a submission, with latest decisions.

    Auth: submitter, or any approver with research or IT role, or admin.
    """
    submission = await get_by_id(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    if not _can_view(principal, submission):
        raise ForbiddenError(
            public_message="You do not have permission to view findings for this submission."
        )

    findings = await detection_finding_store.list_findings_with_latest_decision(
        session, submission_id
    )
    return [FindingResponse(**f) for f in findings]


@router.post(
    "/{submission_id}/findings/{finding_id}/decisions",
    status_code=201,
    response_model=DecisionResponse,
)
async def record_decision(
    submission_id: UUID,
    finding_id: UUID,
    body: DecisionRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> DecisionResponse:
    """Record a researcher decision on a detection finding.

    Auth: submitter or admin.

    After the last open error finding is resolved (accept/override/auto_fix),
    transitions submission back to awaiting_scan and emits detection_resolved.
    """
    # 1. Load submission
    submission = await get_by_id(session, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    if not _can_decide(principal, submission):
        raise ForbiddenError(
            public_message="You do not have permission to record decisions for this submission."
        )

    # 2. Verify finding belongs to this submission
    findings_plain = await detection_finding_store.list_findings_by_submission(
        session, submission_id
    )
    finding = next((f for f in findings_plain if f.id == finding_id), None)
    if finding is None:
        raise HTTPException(
            status_code=404,
            detail="Finding not found for this submission",
        )

    # 3. Insert decision row (append-only)
    decision_row = await detection_finding_store.insert_decision(
        session,
        detection_finding_id=finding_id,
        decision=body.decision,
        actor_principal_id=principal.oid,
        notes=body.notes,
    )

    # 4. Check if all error findings are now resolved
    findings_with_decisions = await detection_finding_store.list_findings_with_latest_decision(
        session, submission_id
    )
    resolved = needs_user_action_resolved(findings_with_decisions)

    if resolved and submission.status == SubmissionStatus.needs_user_action:
        new_status = transition(FsmStatus(submission.status), "detection_resolved")
        submission.status = new_status  # type: ignore[assignment]
        session.add(submission)
        await session.flush()

        approval_event = ApprovalEvent(
            submission_id=submission.id,
            kind="detection_resolved",
            actor_principal_id=principal.oid,
            payload={"decision_id": str(decision_row.id)},
        )
        session.add(approval_event)
        await session.flush()

        logger.info(
            "detection_resolved",
            submission_id=str(submission_id),
            actor=str(principal.oid),
        )

        # Re-dispatch the pipeline now that all blocking findings are resolved.
        settings = get_settings()
        dispatch_fn = _make_redispatch_fn(settings)
        if dispatch_fn is not None:
            from rac_control_plane.services.pipeline_dispatch.payload import (
                build_dispatch_payload,
            )
            secret_name = f"rac-pipeline-cb-{submission.id}"
            # Build payload before session.commit() to avoid expired-attribute lazy loads.
            payload = build_dispatch_payload(
                submission,
                callback_base_url=settings.callback_base_url,
                callback_secret_name=secret_name,
            )

            async def _safe_dispatch(p: dict[str, Any]) -> None:
                """Wrap dispatch so a GitHub error doesn't propagate to the client."""
                try:
                    await dispatch_fn(p)
                except Exception as _exc:
                    logger.error(
                        "detection_resolved_redispatch_failed",
                        submission_id=str(submission_id),
                        error=str(_exc),
                    )

            background_tasks.add_task(_safe_dispatch, payload)

    # Commit the transaction so the GET immediately after sees the new data
    await session.commit()

    logger.info(
        "detection_decision_recorded",
        finding_id=str(finding_id),
        decision=body.decision,
        actor=str(principal.oid),
    )

    return DecisionResponse(
        decision_id=decision_row.id,
        detection_finding_id=finding_id,
        decision=decision_row.decision,  # type: ignore[arg-type]
        decision_actor_principal_id=decision_row.decision_actor_principal_id,
        decision_notes=decision_row.decision_notes,
        created_at=decision_row.created_at,
    )
