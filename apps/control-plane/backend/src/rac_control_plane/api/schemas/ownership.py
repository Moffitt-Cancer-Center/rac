# pattern: type-only
"""Pydantic schemas for ownership endpoints."""

from uuid import UUID

from pydantic import BaseModel


class OwnershipTransferRequest(BaseModel):
    """Body for POST /admin/apps/{app_id}/ownership/transfer."""

    new_pi_principal_id: UUID
    new_dept_fallback: str
    justification: str
