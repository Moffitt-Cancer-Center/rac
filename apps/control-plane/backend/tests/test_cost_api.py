"""Integration tests for cost API endpoints.

Verifies:
- GET /admin/cost/summary → correct grouping from seeded snapshots.
- GET /admin/cost/idle → correct filter (only apps with snapshots + idle).
- Non-admin → 403.

Verifies: rac-v1.AC11.2, rac-v1.AC11.3
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    App,
    CostSnapshotMonthly,
    Submission,
    SubmissionStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=["it_approver"])


def _user_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=[])


async def _insert_snapshot(
    db: AsyncSession,
    *,
    app_slug: str,
    year_month: str,
    cost_usd: float = 0.0,
    untagged_usd: float = 0.0,
) -> CostSnapshotMonthly:
    snap = CostSnapshotMonthly(
        app_slug=app_slug,
        year_month=year_month,
        cost_usd=cost_usd,
        untagged_usd=untagged_usd,
    )
    db.add(snap)
    await db.commit()
    return snap


async def _insert_app_with_last_request(
    db: AsyncSession,
    *,
    slug: str,
    last_request_at: datetime | None = None,
) -> App:
    pi = uuid4()
    sub = Submission(
        slug=slug,
        status=SubmissionStatus.deployed,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=pi,
        dept_fallback="Test",
    )
    db.add(sub)
    await db.flush()

    app = App(
        slug=slug,
        pi_principal_id=pi,
        dept_fallback="Test",
        current_submission_id=sub.id,
        target_port=8000,
        last_request_at=last_request_at,
    )
    db.add(app)
    await db.commit()
    return app


# ---------------------------------------------------------------------------
# Tests: GET /admin/cost/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_summary_returns_grouped_data(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Seed 3 snapshots → summary returns all 3, sorted desc."""
    admin_oid = uuid4()
    # Use UUID-derived app_slug names to avoid cross-test unique-key collisions
    # (db_setup does not rollback between tests).
    run_id = uuid4().hex[:8]
    year_month = "2099-01"  # far-future month unlikely to collide
    slug_a = f"ta-{run_id}"
    slug_b = f"tb-{run_id}"
    slug_c = f"tc-{run_id}"

    await _insert_snapshot(db_setup, app_slug=slug_a, year_month=year_month, cost_usd=100.0)
    await _insert_snapshot(db_setup, app_slug=slug_b, year_month=year_month, cost_usd=50.0)
    await _insert_snapshot(db_setup, app_slug=slug_c, year_month=year_month, cost_usd=200.0)

    response = await client.get(
        f"/admin/cost/summary?year_month={year_month}",
        headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["year_month"] == year_month

    # We may have rows from other tests in the same year_month; filter to ours
    our_slugs = {slug_a, slug_b, slug_c}
    our_rows = [r for r in body["rows"] if r["app_slug"] in our_slugs]
    assert len(our_rows) == 3

    # Sorted descending by total_usd
    assert our_rows[0]["app_slug"] == slug_c
    assert our_rows[0]["total_usd"] == pytest.approx(200.0)
    assert our_rows[1]["app_slug"] == slug_a
    assert our_rows[2]["app_slug"] == slug_b


@pytest.mark.asyncio
async def test_cost_summary_non_admin_403(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    user_oid = uuid4()
    response = await client.get(
        "/admin/cost/summary?year_month=2026-04",
        headers={"Authorization": f"Bearer {_user_token(mock_oidc, user_oid)}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cost_summary_untagged_in_grand_total(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Untagged costs appear in grand_total_usd but not in the app rows."""
    admin_oid = uuid4()
    # Use unique slugs + a unique year_month to avoid conflicts
    run_id = uuid4().hex[:8]
    # Use a year_month that won't exist in other tests
    year_month = f"2097-{(int(run_id[:2], 16) % 12) + 1:02d}"
    slug_a = f"ua2-{run_id}"

    await _insert_snapshot(db_setup, app_slug=slug_a, year_month=year_month, cost_usd=100.0)
    # Insert _untagged row for this specific year_month (unique year_month avoids conflict)
    await _insert_snapshot(
        db_setup, app_slug="_untagged", year_month=year_month,
        cost_usd=0.0, untagged_usd=25.0,
    )

    response = await client.get(
        f"/admin/cost/summary?year_month={year_month}",
        headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["untagged_usd"] == pytest.approx(25.0)
    assert body["grand_total_usd"] == pytest.approx(125.0)

    # Only slug_a in rows (not _untagged)
    row_slugs = [r["app_slug"] for r in body["rows"]]
    assert slug_a in row_slugs
    assert "_untagged" not in row_slugs


# ---------------------------------------------------------------------------
# Tests: GET /admin/cost/idle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_idle_returns_idle_apps(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """App with last_request_at > 30 days ago + cost snapshot → appears in idle."""
    admin_oid = uuid4()
    now = datetime.now(timezone.utc)
    year_month = now.strftime("%Y-%m")

    # Idle app (last request 40 days ago)
    await _insert_app_with_last_request(
        db_setup,
        slug="idle-app",
        last_request_at=now - timedelta(days=40),
    )
    await _insert_snapshot(db_setup, app_slug="idle-app", year_month=year_month, cost_usd=60.0)

    response = await client.get(
        "/admin/cost/idle",
        headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
    )

    assert response.status_code == 200
    body = response.json()
    slugs = [item["app_slug"] for item in body]
    assert "idle-app" in slugs

    idle_row = next(item for item in body if item["app_slug"] == "idle-app")
    assert idle_row["days_idle"] == 40


@pytest.mark.asyncio
async def test_cost_idle_excludes_active_apps(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """App with last_request_at 1 day ago NOT in idle list."""
    admin_oid = uuid4()
    now = datetime.now(timezone.utc)
    year_month = now.strftime("%Y-%m")

    await _insert_app_with_last_request(
        db_setup,
        slug="active-app",
        last_request_at=now - timedelta(days=1),
    )
    await _insert_snapshot(db_setup, app_slug="active-app", year_month=year_month, cost_usd=60.0)

    response = await client.get(
        "/admin/cost/idle",
        headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
    )

    assert response.status_code == 200
    slugs = [item["app_slug"] for item in response.json()]
    assert "active-app" not in slugs


@pytest.mark.asyncio
async def test_cost_idle_non_admin_403(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    user_oid = uuid4()
    response = await client.get(
        "/admin/cost/idle",
        headers={"Authorization": f"Bearer {_user_token(mock_oidc, user_oid)}"},
    )
    assert response.status_code == 403
