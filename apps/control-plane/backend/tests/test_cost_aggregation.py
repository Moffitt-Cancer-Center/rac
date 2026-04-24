"""Unit + property tests for cost aggregation (pure functions).

All tests are I/O-free — no DB, no mocks needed.

Verifies: rac-v1.AC11.2, rac-v1.AC11.3
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rac_control_plane.services.cost.aggregation import (
    CostSnapshot,
    CostSummary,
    CostSummaryRow,
    IdleApp,
    compute_cost_summary,
    compute_idle_apps,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
YEAR_MONTH = "2026-04"


def snap(app_slug: str, cost: str, year_month: str = YEAR_MONTH) -> CostSnapshot:
    return CostSnapshot(app_slug=app_slug, year_month=year_month, cost_usd=Decimal(cost))


# ---------------------------------------------------------------------------
# Test 1: compute_cost_summary — basic correctness
# ---------------------------------------------------------------------------


def test_compute_cost_summary_groups_and_sorts() -> None:
    """Multiple snapshots for same app are summed; result sorted desc."""
    snapshots = [
        snap("app-b", "10.00"),
        snap("app-a", "50.00"),
        snap("app-a", "25.00"),  # second row for app-a
        snap("app-c", "5.00"),
    ]
    summary = compute_cost_summary(snapshots)

    assert summary.year_month == YEAR_MONTH
    assert len(summary.rows) == 3

    # sorted descending: app-a (75), app-b (10), app-c (5)
    assert summary.rows[0].app_slug == "app-a"
    assert summary.rows[0].total_usd == Decimal("75.00")
    assert summary.rows[1].app_slug == "app-b"
    assert summary.rows[1].total_usd == Decimal("10.00")
    assert summary.rows[2].app_slug == "app-c"
    assert summary.rows[2].total_usd == Decimal("5.00")


def test_compute_cost_summary_grand_total_includes_untagged() -> None:
    """grand_total_usd = sum of rows + untagged_usd."""
    snapshots = [snap("app-a", "100.00"), snap("app-b", "50.00")]
    untagged = Decimal("15.00")
    summary = compute_cost_summary(snapshots, untagged_usd=untagged)

    assert summary.grand_total_usd == Decimal("165.00")
    assert summary.untagged_usd == Decimal("15.00")


def test_compute_cost_summary_empty_snapshots() -> None:
    """Empty input returns empty summary with grand_total = untagged_usd."""
    summary = compute_cost_summary([], untagged_usd=Decimal("20.00"))

    assert summary.rows == []
    assert summary.grand_total_usd == Decimal("20.00")
    assert summary.untagged_usd == Decimal("20.00")


# ---------------------------------------------------------------------------
# Test 2: Property — sum(rows) + untagged == grand_total
# ---------------------------------------------------------------------------


@given(
    costs=st.lists(
        st.decimals(min_value=Decimal("0"), max_value=Decimal("9999"), places=2),
        min_size=0,
        max_size=20,
    ),
    untagged=st.decimals(min_value=Decimal("0"), max_value=Decimal("9999"), places=2),
    slugs=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=10),
        min_size=0,
        max_size=20,
    ),
)
@settings(max_examples=100)
def test_grand_total_invariant(
    costs: list[Decimal], untagged: Decimal, slugs: list[str]
) -> None:
    """sum(row.total_usd for row in rows) + untagged == grand_total."""
    # Pair each cost with a slug (cycle through slugs)
    if not costs:
        return
    pairs = [(slugs[i % len(slugs)] if slugs else "x", c) for i, c in enumerate(costs)]
    snapshots = [
        CostSnapshot(app_slug=s, year_month=YEAR_MONTH, cost_usd=c)
        for s, c in pairs
    ]

    summary = compute_cost_summary(snapshots, untagged_usd=untagged)

    row_sum = sum((r.total_usd for r in summary.rows), Decimal(0))
    assert row_sum + summary.untagged_usd == summary.grand_total_usd


# ---------------------------------------------------------------------------
# Test 3: Property — stability under permutation
# ---------------------------------------------------------------------------


def test_compute_cost_summary_stable_under_permutation() -> None:
    """Same output for different input ordering."""
    snapshots_a = [snap("app-a", "30"), snap("app-b", "20"), snap("app-a", "10")]
    snapshots_b = [snap("app-a", "10"), snap("app-a", "30"), snap("app-b", "20")]

    summary_a = compute_cost_summary(snapshots_a)
    summary_b = compute_cost_summary(snapshots_b)

    assert summary_a.rows == summary_b.rows
    assert summary_a.grand_total_usd == summary_b.grand_total_usd


# ---------------------------------------------------------------------------
# Test 4: compute_idle_apps — app exactly at threshold is included
# ---------------------------------------------------------------------------


def test_idle_apps_exactly_at_threshold_included() -> None:
    """App with last_request_at exactly idle_threshold_days ago is included."""
    threshold_days = 30
    last_request_at = NOW - timedelta(days=threshold_days)

    snapshots = [snap("app-a", "60.00")]
    app_last_requests = [("app-a", last_request_at)]

    idle = compute_idle_apps(
        app_last_requests,
        snapshots,
        now=NOW,
        idle_threshold_days=threshold_days,
    )

    assert len(idle) == 1
    assert idle[0].app_slug == "app-a"
    assert idle[0].days_idle == threshold_days


def test_idle_apps_active_yesterday_excluded() -> None:
    """App with last_request_at 1 day ago is NOT included (< 30 days)."""
    snapshots = [snap("app-a", "60.00")]
    app_last_requests = [("app-a", NOW - timedelta(days=1))]

    idle = compute_idle_apps(app_last_requests, snapshots, now=NOW)

    assert idle == []


def test_idle_apps_none_last_request_included() -> None:
    """App with last_request_at=None qualifies (treat as infinitely idle)."""
    snapshots = [snap("app-a", "90.00")]
    app_last_requests = [("app-a", None)]

    idle = compute_idle_apps(app_last_requests, snapshots, now=NOW)

    assert len(idle) == 1
    assert idle[0].days_idle == 30  # pinned at threshold


def test_idle_apps_no_snapshot_excluded() -> None:
    """App with no cost snapshot is NOT in IdleApp list (not deployed)."""
    snapshots: list[CostSnapshot] = []  # no snapshots
    app_last_requests = [("app-a", None)]  # would be idle if deployed

    idle = compute_idle_apps(app_last_requests, snapshots, now=NOW)

    assert idle == []


def test_idle_apps_estimated_savings_calculation() -> None:
    """estimated_monthly_savings_usd = (monthly_cost / 30) * 30."""
    snapshots = [snap("app-a", "120.00")]
    app_last_requests = [("app-a", NOW - timedelta(days=45))]

    idle = compute_idle_apps(app_last_requests, snapshots, now=NOW)

    assert len(idle) == 1
    # 120 / 30 * 30 = 120
    assert idle[0].estimated_monthly_savings_usd == Decimal("120.00")


def test_idle_apps_sorted_by_days_idle_desc() -> None:
    """IdleApp list is sorted by days_idle descending."""
    snapshots = [snap("app-a", "10"), snap("app-b", "10"), snap("app-c", "10")]
    app_last_requests = [
        ("app-a", NOW - timedelta(days=31)),
        ("app-b", NOW - timedelta(days=60)),
        ("app-c", NOW - timedelta(days=90)),
    ]

    idle = compute_idle_apps(app_last_requests, snapshots, now=NOW)

    assert len(idle) == 3
    assert idle[0].app_slug == "app-c"  # 90 days
    assert idle[1].app_slug == "app-b"  # 60 days
    assert idle[2].app_slug == "app-a"  # 31 days
