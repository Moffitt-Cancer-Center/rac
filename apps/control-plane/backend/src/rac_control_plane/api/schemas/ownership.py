# pattern: type-only
"""Pydantic schemas for ownership endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class OwnershipTransferRequest(BaseModel):
    """Body for POST /admin/apps/{app_id}/ownership/transfer."""

    new_pi_principal_id: UUID
    new_dept_fallback: str
    justification: str


class OwnershipFlagResponse(BaseModel):
    """One open ownership flag (no corresponding review row)."""

    flag_id: UUID
    app_id: UUID
    app_slug: str
    pi_principal_id: UUID
    pi_display_name: str | None
    reason: Literal["account_disabled", "not_found"]
    flagged_at: datetime

    model_config = {"from_attributes": True}


OwnershipFlagListResponse = list[OwnershipFlagResponse]
