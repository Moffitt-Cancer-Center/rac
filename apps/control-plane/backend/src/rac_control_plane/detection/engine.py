# pattern: Imperative Shell
"""Detection engine — orchestrates repo_context build, rule evaluation, and persistence.

This is the outer Imperative Shell. It:
  1. Builds a RepoContext (git clone via repo_context.build_repo_context).
  2. Loads rules (from app.state.rules if available, else calls load_rules()).
  3. Evaluates all rules (pure, via evaluate.run_all).
  4. Persists each finding (append-only, via detection_finding_store.insert_finding).
  5. Applies FSM transition to needs_user_action if appropriate (AC4.5).
  6. Emits an approval_event.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data import detection_finding_store
from rac_control_plane.data.models import ApprovalEvent, DetectionFinding, Submission, SubmissionStatus
from rac_control_plane.detection import evaluate
from rac_control_plane.detection.contracts import Finding, Rule

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


async def run_detection(
    session: AsyncSession,
    submission: Submission,
    workdir: Path | None = None,
    *,
    rules: dict[str, Rule] | None = None,
    principal_kind: str = "user",
    _prebuilt_repo_root: Path | None = None,
) -> list[DetectionFinding]:
    """Run detection rules against a submission.

    Args:
        session: SQLAlchemy async session (must be within a transaction context).
        submission: ORM Submission row.
        workdir: Scratch directory for git clone. Uses a tmpdir if None.
        rules: Override rules dict (useful in tests). If None, loads from
               discovery.load_rules() (or from app.state.rules).
        principal_kind: 'user' or 'agent' — determines whether detection
                        findings trigger needs_user_action (AC4.5).
        _prebuilt_repo_root: Skip clone; use this path directly (test helper).

    Returns:
        List of inserted DetectionFinding ORM rows.
    """
    from rac_control_plane.detection.discovery import load_rules
    from rac_control_plane.detection.repo_context import build_repo_context

    # Step 1: Load rules
    if rules is None:
        try:
            from rac_control_plane.main import app as _app  # type: ignore[attr-defined]
            rules = getattr(_app.state, "rules", None)
        except Exception:  # noqa: BLE001
            rules = None
        if rules is None:
            rules = load_rules()

    rule_list = list(rules.values())

    # Step 2: Build RepoContext (may raise RepoContextError)
    use_tmpdir = workdir is None
    tmp = None
    if use_tmpdir:
        tmp = tempfile.mkdtemp(prefix="rac_detection_")
        workdir = Path(tmp)

    try:
        ctx = await build_repo_context(
            submission,
            workdir,
            _prebuilt_repo_root=_prebuilt_repo_root,
        )
    except Exception as exc:
        logger.error(
            "detection_repo_context_failed",
            submission_id=str(submission.id),
            error=str(exc),
        )
        raise

    # Step 3: Evaluate rules (pure)
    findings: list[Finding] = evaluate.run_all(rule_list, ctx)

    # Step 4: Persist each finding
    inserted: list[DetectionFinding] = []
    for finding in findings:
        row = await detection_finding_store.insert_finding(
            session, submission.id, finding
        )
        inserted.append(row)

    # Step 5: FSM transition logic
    has_warn_or_error = any(f.severity in ("warn", "error") for f in findings)
    has_error = any(f.severity == "error" for f in findings)

    should_transition = (
        # AC4.5: agent + any warn/error finding → needs_user_action
        (principal_kind == "agent" and has_warn_or_error)
        # Interactive user + error finding → needs_user_action
        or (principal_kind != "agent" and has_error)
    )

    if should_transition and submission.status == SubmissionStatus.awaiting_scan:
        from rac_control_plane.services.submissions.fsm import transition
        new_status = transition(submission.status, "detection_needs_user_action")
        submission.status = new_status
        session.add(submission)
        await session.flush()

        # Use submission's submitter principal as the system actor for this event
        approval_event = ApprovalEvent(
            submission_id=submission.id,
            kind="detection_needs_user_action",
            actor_principal_id=submission.submitter_principal_id,
            payload={"finding_count": len(findings)},
        )
        session.add(approval_event)
        await session.flush()

        logger.info(
            "detection_needs_user_action",
            submission_id=str(submission.id),
            finding_count=len(findings),
            principal_kind=principal_kind,
        )

    logger.info(
        "detection_completed",
        submission_id=str(submission.id),
        findings=len(inserted),
        transitioned=should_transition,
    )
    return inserted
