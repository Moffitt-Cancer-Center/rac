# pattern: type-only
"""Pydantic request/response schemas for the token management API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TokenCreateRequest(BaseModel):
    """Body for POST /apps/{app_id}/tokens."""
    reviewer_label: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable label for this token (e.g. 'Reviewer #1').",
    )
    ttl_days: int = Field(
        ...,
        ge=1,
        le=180,
        description="Token validity in days (1–180).",
    )


class TokenCreateResponse(BaseModel):
    """Response for POST /apps/{app_id}/tokens (one-time — JWT is not re-fetchable)."""
    jwt: str
    jti: UUID
    expires_at: datetime
    reviewer_label: str
    visit_url: str


class TokenListItemResponse(BaseModel):
    """A single token in the listing (JWT omitted for security)."""
    jti: UUID
    reviewer_label: str | None
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    scope: str
    issued_by_principal_id: UUID | None


class TokenListResponse(BaseModel):
    """Response for GET /apps/{app_id}/tokens."""
    items: list[TokenListItemResponse]
