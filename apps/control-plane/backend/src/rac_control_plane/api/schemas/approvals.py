# pattern: type-only
"""Pydantic schemas for approval endpoints."""

from typing import Literal

from pydantic import BaseModel


class ApprovalRequest(BaseModel):
    """Body for POST /submissions/{id}/approvals/{stage}."""

    decision: Literal["approve", "reject", "request_changes"]
    notes: str | None = None
