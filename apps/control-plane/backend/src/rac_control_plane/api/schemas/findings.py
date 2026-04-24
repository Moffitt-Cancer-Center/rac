"""Type-only Pydantic schemas for the findings API endpoints.

No FCIS tag: type-only schema modules are exempt per project convention.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class FindingResponse(BaseModel):
    """Single detection finding with its latest decision (if any)."""

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
    created_at: datetime
    # Decision fields (None if no decision yet)
    latest_decision: Literal["accept", "override", "auto_fix", "dismiss"] | None = None
    decision_actor_principal_id: UUID | None = None
    decision_notes: str | None = None
    decision_at: datetime | None = None
    decision_id: UUID | None = None

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
