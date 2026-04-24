# type-only — Pydantic response schemas for access log viewer.
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AccessLogItem(BaseModel):
    """One access_log row returned by the viewer API."""

    id: UUID
    created_at: datetime
    reviewer_token_jti: str | None
    reviewer_label: str | None
    access_mode: str | None
    method: str | None
    path: str | None
    upstream_status: int | None
    latency_ms: int | None
    source_ip: str | None


class AccessLogListResponse(BaseModel):
    """Paginated response for GET /apps/{app_id}/access-log."""

    items: list[AccessLogItem]
    next_cursor: UUID | None
