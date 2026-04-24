"""Type-only Pydantic schemas for the findings API endpoints.

No FCIS tag: type-only schema modules are exempt per project convention.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class FindingDecisionResponse(BaseModel):
    """Nested decision object embedded inside FindingResponse."""

    id: UUID
    detection_finding_id: UUID
    decision: Literal["accept", "override", "auto_fix", "dismiss"]
    decision_actor_principal_id: UUID
    decision_notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class FindingResponse(BaseModel):
    """Single detection finding with its latest decision (if any) nested.

    The ``decision`` field is a nested object (or None if no decision has been
    recorded yet), matching the frontend ``findingWithDecisionSchema`` in
    ``features/nudges/types.ts``.  Flat ``latest_decision`` / ``decision_at``
    fields have been removed — consumers should read from ``decision.decision``
    and ``decision.created_at`` respectively.
    """

    id: UUID
    submission_id: UUID
    rule_id: str
    rule_version: int
    severity: Literal["info", "warn", "error"]
    title: str
    detail: str
    file_path: str | None = None
    line_ranges: list[list[int]] | None = None
    auto_fix: dict[str, Any] | None = None
    suggested_action: Literal["accept", "override", "auto_fix", "dismiss"] | None = None
    created_at: datetime
    # Nested decision (None if no decision recorded yet)
    decision: FindingDecisionResponse | None = None

    model_config = {"from_attributes": True}


class DecisionRequest(BaseModel):
    """Request body for recording a decision on a finding."""

    decision: Literal["accept", "override", "auto_fix", "dismiss"]
    notes: str | None = None


class DecisionResponse(BaseModel):
    """Response body after a decision is recorded."""

    decision_id: UUID
    detection_finding_id: UUID
    decision: Literal["accept", "override", "auto_fix", "dismiss"]
    decision_actor_principal_id: UUID
    decision_notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
