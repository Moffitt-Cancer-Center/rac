# pattern: type-only
"""Pydantic schemas for cost management API endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CostSummaryRowResponse(BaseModel):
    """Per-app cost total for a given month."""

    app_slug: str
    total_usd: float


class CostSummaryResponse(BaseModel):
    """Month-to-date cost summary for all apps.

    Verifies: rac-v1.AC11.2
    """

    year_month: str
    rows: list[CostSummaryRowResponse]
    grand_total_usd: float
    untagged_usd: float


class IdleAppResponse(BaseModel):
    """An app idle for >= 30 days with cost estimate.

    Verifies: rac-v1.AC11.3
    """

    app_slug: str
    last_request_at: datetime | None
    days_idle: int
    estimated_monthly_savings_usd: float
