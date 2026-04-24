# pattern: Functional Core
"""Pure cost aggregation functions.

All functions are free of I/O and side effects.

Verifies: rac-v1.AC11.2, rac-v1.AC11.3
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CostSnapshot:
    """A single cost snapshot row (one app, one month)."""

    app_slug: str
    year_month: str      # YYYY-MM
    cost_usd: Decimal


@dataclass(frozen=True)
class CostSummaryRow:
    """Aggregated cost for a single app within a month."""

    app_slug: str
    total_usd: Decimal


@dataclass(frozen=True)
class CostSummary:
    """Aggregated cost summary for a given month."""

    year_month: str
    rows: list[CostSummaryRow]   # sorted by total_usd descending
    grand_total_usd: Decimal
    untagged_usd: Decimal        # cost without rac_app_slug tag


@dataclass(frozen=True)
class IdleApp:
    """An app that has had no requests for >= idle_threshold_days.

    estimated_monthly_savings_usd: average daily cost × 30.
    """

    app_slug: str
    last_request_at: datetime | None
    days_idle: int
    estimated_monthly_savings_usd: Decimal


def compute_cost_summary(
    snapshots: list[CostSnapshot],
    *,
    untagged_usd: Decimal = Decimal(0),
) -> CostSummary:
    """Group snapshots by app_slug, sum, sort descending by total_usd.

    Pure: no I/O, no side effects.

    Args:
        snapshots: List of CostSnapshot items.  All should share the same
                   year_month; if they differ the first year_month wins.
        untagged_usd: Unattributed cost (rows with no rac_app_slug tag).

    Returns:
        CostSummary with rows sorted by total_usd desc and correct grand total.
    """
    if not snapshots:
        return CostSummary(
            year_month="",
            rows=[],
            grand_total_usd=untagged_usd,
            untagged_usd=untagged_usd,
        )

    year_month = snapshots[0].year_month

    # Aggregate per app_slug
    totals: dict[str, Decimal] = {}
    for snap in snapshots:
        totals[snap.app_slug] = totals.get(snap.app_slug, Decimal(0)) + snap.cost_usd

    rows = [
        CostSummaryRow(app_slug=slug, total_usd=total)
        for slug, total in totals.items()
    ]
    rows.sort(key=lambda r: r.total_usd, reverse=True)

    grand_total = sum(totals.values(), Decimal(0)) + untagged_usd

    return CostSummary(
        year_month=year_month,
        rows=rows,
        grand_total_usd=grand_total,
        untagged_usd=untagged_usd,
    )


def compute_idle_apps(
    app_last_requests: list[tuple[str, datetime | None]],
    snapshots: list[CostSnapshot],
    *,
    now: datetime,
    idle_threshold_days: int = 30,
) -> list[IdleApp]:
    """Identify deployed apps that have been idle for >= idle_threshold_days.

    An app qualifies if:
    - It has a cost snapshot in the current month (i.e., it is deployed and
      incurring cost — apps with no snapshot at all are NOT included because
      they may have never been deployed).
    - Its last_request_at is None OR is older than idle_threshold_days from now.

    Args:
        app_last_requests: List of (app_slug, last_request_at) tuples for all
                           currently deployed apps.
        snapshots: Cost snapshots for the current month (used to compute
                   estimated_monthly_savings_usd and to filter to deployed apps).
        now: The reference "now" datetime (injected for determinism).
        idle_threshold_days: Threshold for idle detection.

    Returns:
        List of IdleApp, sorted by days_idle descending.
    """
    # Build a lookup from app_slug → list of cost snapshots
    slug_to_cost: dict[str, Decimal] = {}
    for snap in snapshots:
        slug_to_cost[snap.app_slug] = (
            slug_to_cost.get(snap.app_slug, Decimal(0)) + snap.cost_usd
        )

    idle: list[IdleApp] = []

    for app_slug, last_request_at in app_last_requests:
        # Skip apps with no cost snapshot — they are not deployed (Phase 5: all
        # deployed apps have a snapshot entry from the ingest job; apps without
        # cost data are excluded per spec).
        if app_slug not in slug_to_cost:
            continue

        if last_request_at is None:
            days_idle = idle_threshold_days  # treat None as "infinitely idle"
            qualifies = True
        else:
            delta = now - last_request_at
            days_idle = int(delta.total_seconds() // 86400)
            qualifies = days_idle >= idle_threshold_days

        if qualifies:
            # Phase 5 simplification: the savings figure equals the full monthly
            # cost (i.e., we'd save everything by deleting the app). Once Phase 6
            # populates last_request_at from real Shim traffic, this should become
            # (idle_days / 30) × monthly_cost or similar so partially-active apps
            # don't over-attribute savings.
            estimated_savings = slug_to_cost[app_slug]

            idle.append(
                IdleApp(
                    app_slug=app_slug,
                    last_request_at=last_request_at,
                    days_idle=days_idle,
                    estimated_monthly_savings_usd=estimated_savings,
                )
            )

    idle.sort(key=lambda a: a.days_idle, reverse=True)
    return idle
