# pattern: Imperative Shell
"""Cost management API routes.

Endpoints:
  GET /admin/cost/summary?year_month=YYYY-MM
  GET /admin/cost/idle

Admin-only. Returns per-app cost data and idle-app analysis.

Note on last_request_at (AC11.3):
  The app.last_request_at column is populated by the Shim (Phase 6) when
  HTTP requests arrive at the proxy.  For Phase 5, the column is always NULL
  (added by migration 0008 with DEFAULT NULL), so all deployed apps that have
  cost snapshots appear in the idle list.  This is correct Phase-5 behavior;
  Phase 6 will start populating the column.

Verifies: rac-v1.AC11.2, rac-v1.AC11.3
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.cost import (
    CostSummaryResponse,
    CostSummaryRowResponse,
    IdleAppResponse,
)
from rac_control_plane.auth.dependencies import require_admin
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import App, CostSnapshotMonthly
from rac_control_plane.services.cost.aggregation import (
    CostSnapshot,
    compute_cost_summary,
    compute_idle_apps,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/cost", tags=["cost"])

_YM_RE = re.compile(r"^\d{4}-\d{2}$")


@router.get("/summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    year_month: str = Query(
        ...,
        description="Month to query in YYYY-MM format",
        pattern=r"^\d{4}-\d{2}$",
    ),
    principal: Annotated[Principal, Depends(require_admin)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_session),
) -> CostSummaryResponse:
    """Return per-app month-to-date costs for a given year_month.

    Admin-only.

    Args:
        year_month: Month in YYYY-MM format (e.g., 2026-04).
        principal: Authenticated admin principal.
        session: Database session.

    Returns:
        CostSummaryResponse with per-app rows sorted by total_usd desc.

    Raises:
        400: year_month format is invalid.
        403: Principal is not an admin.

    Verifies: rac-v1.AC11.2
    """
    result = await session.execute(
        select(CostSnapshotMonthly).where(
            CostSnapshotMonthly.year_month == year_month
        )
    )
    rows = result.scalars().all()

    # Separate untagged from app snapshots
    tagged_snapshots = [
        CostSnapshot(
            app_slug=row.app_slug,
            year_month=row.year_month,
            cost_usd=Decimal(str(row.cost_usd)),
        )
        for row in rows
        if row.app_slug != "_untagged"
    ]

    untagged_usd = sum(
        (Decimal(str(row.untagged_usd)) + Decimal(str(row.cost_usd)))
        for row in rows
        if row.app_slug == "_untagged"
    ) or Decimal(0)

    summary = compute_cost_summary(tagged_snapshots, untagged_usd=untagged_usd)

    return CostSummaryResponse(
        year_month=year_month,
        rows=[
            CostSummaryRowResponse(app_slug=r.app_slug, total_usd=float(r.total_usd))
            for r in summary.rows
        ],
        grand_total_usd=float(summary.grand_total_usd),
        untagged_usd=float(summary.untagged_usd),
    )


@router.get("/idle", response_model=list[IdleAppResponse])
async def get_idle_apps(
    principal: Annotated[Principal, Depends(require_admin)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_session),
) -> list[IdleAppResponse]:
    """Return apps that have been idle for >= 30 days.

    An app qualifies if it has a cost snapshot for the current month AND its
    last_request_at is NULL or older than 30 days.

    Note: app.last_request_at is populated by the Shim (Phase 6).  In Phase 5
    the column is always NULL, so all deployed apps with cost snapshots qualify.

    Admin-only.

    Args:
        principal: Authenticated admin principal.
        session: Database session.

    Returns:
        List of IdleAppResponse sorted by days_idle descending.

    Raises:
        403: Principal is not an admin.

    Verifies: rac-v1.AC11.3
    """
    now = datetime.now(UTC)
    current_year_month = now.strftime("%Y-%m")

    # Get all deployed apps and their last_request_at
    apps_result = await session.execute(
        select(App.slug, App.last_request_at)
    )
    app_rows = apps_result.all()
    app_last_requests: list[tuple[str, datetime | None]] = [
        (row.slug, row.last_request_at)
        for row in app_rows
    ]

    # Get cost snapshots for current month
    snapshots_result = await session.execute(
        select(CostSnapshotMonthly).where(
            CostSnapshotMonthly.year_month == current_year_month,
            CostSnapshotMonthly.app_slug != "_untagged",
        )
    )
    snapshot_rows = snapshots_result.scalars().all()
    snapshots = [
        CostSnapshot(
            app_slug=row.app_slug,
            year_month=row.year_month,
            cost_usd=Decimal(str(row.cost_usd)),
        )
        for row in snapshot_rows
    ]

    idle_apps = compute_idle_apps(
        app_last_requests,
        snapshots,
        now=now,
        idle_threshold_days=30,
    )

    return [
        IdleAppResponse(
            app_slug=app.app_slug,
            last_request_at=app.last_request_at,
            days_idle=app.days_idle,
            estimated_monthly_savings_usd=float(app.estimated_monthly_savings_usd),
        )
        for app in idle_apps
    ]
