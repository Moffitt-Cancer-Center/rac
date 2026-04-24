# pattern: Imperative Shell
"""Append-only store for DetectionFinding and DetectionFindingDecision rows.

Both tables are append-only per AC12.1; this store only exposes INSERT and
SELECT (no UPDATE, no DELETE).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import DetectionFinding, DetectionFindingDecision
from rac_control_plane.detection.contracts import Finding

logger = structlog.get_logger(__name__)


def _finding_to_jsonb(value: object) -> Any:
    """Convert a Finding field to a JSONB-serialisable value."""
    if value is None:
        return None
    # For tuple-of-tuples (line_ranges) → [[start, end], ...]
    if isinstance(value, tuple):
        return [list(pair) if isinstance(pair, tuple) else pair for pair in value]
    # For AutoFixAction dataclass → dict
    try:
        return asdict(value)  # type: ignore[arg-type]
    except (TypeError, AttributeError):
        return str(value)


async def insert_finding(
    session: AsyncSession,
    submission_id: UUID,
    finding: Finding,
) -> DetectionFinding:
    """Insert a Finding as an append-only DetectionFinding row.

    Args:
        session: SQLAlchemy async session.
        submission_id: The submission this finding belongs to.
        finding: The Finding dataclass from the rule engine.

    Returns:
        The committed DetectionFinding ORM object.
    """
    row = DetectionFinding(
        submission_id=submission_id,
        rule_id=finding.rule_id,
        rule_version=finding.rule_version,
        severity=finding.severity,
        title=finding.title,
        detail=finding.detail,
        file_path=finding.file_path,
        line_ranges=_finding_to_jsonb(finding.line_ranges) if finding.line_ranges else None,
        auto_fix=_finding_to_jsonb(finding.auto_fix) if finding.auto_fix else None,
    )
    session.add(row)
    await session.flush()
    logger.debug(
        "detection_finding_inserted",
        finding_id=str(row.id),
        rule_id=finding.rule_id,
        severity=finding.severity,
    )
    return row


async def list_findings_by_submission(
    session: AsyncSession,
    submission_id: UUID,
) -> list[DetectionFinding]:
    """Return all DetectionFinding rows for a submission (no decision join).

    For the joined query (with latest decision), use list_findings_with_decisions.
    """
    result = await session.execute(
        select(DetectionFinding)
        .where(DetectionFinding.submission_id == submission_id)
        .order_by(DetectionFinding.created_at)
    )
    return list(result.scalars().all())


async def insert_decision(
    session: AsyncSession,
    detection_finding_id: UUID,
    decision: str,
    actor_principal_id: UUID,
    notes: str | None = None,
) -> DetectionFindingDecision:
    """Insert a new decision for a detection finding.

    Args:
        session: SQLAlchemy async session.
        detection_finding_id: The finding being decided on.
        decision: One of 'accept', 'override', 'auto_fix', 'dismiss'.
        actor_principal_id: Principal making the decision.
        notes: Optional free-text notes.

    Returns:
        The committed DetectionFindingDecision ORM object.
    """
    row = DetectionFindingDecision(
        detection_finding_id=detection_finding_id,
        decision=decision,
        decision_actor_principal_id=actor_principal_id,
        decision_notes=notes,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "detection_finding_decision_inserted",
        decision_id=str(row.id),
        finding_id=str(detection_finding_id),
        decision=decision,
    )
    return row


async def list_findings_with_latest_decision(
    session: AsyncSession,
    submission_id: UUID,
) -> list[dict[str, Any]]:
    """Return findings for a submission, each annotated with its latest decision (or None).

    Returns a list of dicts with keys from DetectionFinding columns plus:
      - latest_decision: str | None
      - decision_actor_principal_id: UUID | None
      - decision_notes: str | None
      - decision_at: datetime | None
    """
    # Load findings
    findings = await list_findings_by_submission(session, submission_id)
    if not findings:
        return []

    finding_ids = [f.id for f in findings]

    # Load all decisions for these findings
    decisions_result = await session.execute(
        select(DetectionFindingDecision)
        .where(DetectionFindingDecision.detection_finding_id.in_(finding_ids))
        .order_by(DetectionFindingDecision.created_at.desc())
    )
    all_decisions = list(decisions_result.scalars().all())

    # Build a map: finding_id → latest_decision
    latest: dict[UUID, DetectionFindingDecision] = {}
    for d in all_decisions:
        if d.detection_finding_id not in latest:
            latest[d.detection_finding_id] = d

    results: list[dict[str, Any]] = []
    for f in findings:
        dec = latest.get(f.id)
        results.append({
            "id": f.id,
            "submission_id": f.submission_id,
            "rule_id": f.rule_id,
            "rule_version": f.rule_version,
            "severity": f.severity,
            "title": f.title,
            "detail": f.detail,
            "file_path": f.file_path,
            "line_ranges": f.line_ranges,
            "auto_fix": f.auto_fix,
            "created_at": f.created_at,
            "latest_decision": dec.decision if dec else None,
            "decision_actor_principal_id": dec.decision_actor_principal_id if dec else None,
            "decision_notes": dec.decision_notes if dec else None,
            "decision_at": dec.created_at if dec else None,
            "decision_id": dec.id if dec else None,
        })
    return results
