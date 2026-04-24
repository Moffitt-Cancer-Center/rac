"""Integration tests for the ownership transfer endpoint and service.

Uses a real Postgres testcontainer + the FastAPI test client fixture.

Verifies:
- AC9.3: Transfer changes app.pi_principal_id but leaves existing approval_event
  rows with their original actor_principal_id unchanged.
- Transfer resolves open 'account_disabled' flags.
- Invalid new PI → 422.
- Non-admin → 403.

Verifies: rac-v1.AC9.3
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    App,
    ApprovalEvent,
    AppOwnershipFlag,
    AppOwnershipFlagReview,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.services.ownership.pi_validation import Invalid, Ok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=["it_approver"])


def _user_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=[])


async def _insert_app_with_submission(
    db_setup: AsyncSession,
    *,
    pi_oid: UUID | None = None,
    slug: str | None = None,
) -> tuple[App, Submission]:
    """Insert a deployed app + submission, committed so the HTTP layer can see them."""
    pi = pi_oid or uuid4()
    slug = slug or f"app-{uuid4().hex[:8]}"

    sub = Submission(
        slug=slug,
        status=SubmissionStatus.deployed,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=pi,
        dept_fallback="Original Dept",
    )
    db_setup.add(sub)
    await db_setup.flush()

    app = App(
        slug=slug,
        pi_principal_id=pi,
        dept_fallback="Original Dept",
        current_submission_id=sub.id,
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
    )
    db_setup.add(app)
    await db_setup.commit()
    return app, sub


async def _insert_approval_event(
    db_setup: AsyncSession,
    *,
    submission_id: UUID,
    actor_oid: UUID,
    kind: str = "research_decision",
) -> ApprovalEvent:
    """Insert an approval_event row, committed."""
    event = ApprovalEvent(
        submission_id=submission_id,
        kind=kind,
        actor_principal_id=actor_oid,
        decision="approve",
        comment=None,
        payload=None,
    )
    db_setup.add(event)
    await db_setup.commit()
    return event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_happy_path(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Admin transfers ownership → app updated, ownership_transferred event inserted,
    old approval_event rows unchanged (AC9.3)."""
    admin_oid = uuid4()
    old_pi = uuid4()
    new_pi = uuid4()

    app, sub = await _insert_app_with_submission(db_setup, pi_oid=old_pi)
    old_event = await _insert_approval_event(
        db_setup, submission_id=sub.id, actor_oid=old_pi
    )

    # Mock Graph validation to say new PI is valid
    with patch(
        "rac_control_plane.services.ownership.transfer._default_validate_pi_fn",
        new=AsyncMock(return_value=Ok()),
    ):
        response = await client.post(
            f"/admin/apps/{app.id}/ownership/transfer",
            json={
                "new_pi_principal_id": str(new_pi),
                "new_dept_fallback": "New Dept",
                "justification": "PI transferred to new department",
            },
            headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pi_principal_id"] == str(new_pi)
    assert body["dept_fallback"] == "New Dept"

    # Verify old approval_event row is untouched (AC9.3)
    evt = await db_setup.get(ApprovalEvent, old_event.id)
    await db_setup.refresh(evt)
    assert evt is not None
    assert evt.actor_principal_id == old_pi  # unchanged


@pytest.mark.asyncio
async def test_transfer_preserves_audit(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """AC9.3: historical actor_principal_id on existing events is never changed."""
    admin_oid = uuid4()
    original_approver = uuid4()
    new_pi = uuid4()

    app, sub = await _insert_app_with_submission(db_setup, pi_oid=uuid4())

    # Insert a research_decision event with original_approver as actor
    old_event = await _insert_approval_event(
        db_setup, submission_id=sub.id, actor_oid=original_approver, kind="research_decision"
    )

    with patch(
        "rac_control_plane.services.ownership.transfer._default_validate_pi_fn",
        new=AsyncMock(return_value=Ok()),
    ):
        r = await client.post(
            f"/admin/apps/{app.id}/ownership/transfer",
            json={
                "new_pi_principal_id": str(new_pi),
                "new_dept_fallback": "Dept B",
                "justification": "Transfer test",
            },
            headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
        )
    assert r.status_code == 200

    # Re-fetch in db_setup session
    await db_setup.refresh(old_event)
    # The research_decision row must still point at the original approver
    assert old_event.actor_principal_id == original_approver

    # A new ownership_transferred event was inserted (different actor = admin_oid)
    new_events_result = await db_setup.execute(
        select(ApprovalEvent).where(
            ApprovalEvent.kind == "ownership_transferred"
        )
    )
    transfer_events = new_events_result.scalars().all()
    assert len(transfer_events) >= 1
    # The latest one should have admin as actor
    latest = max(transfer_events, key=lambda e: e.created_at)
    assert latest.actor_principal_id == admin_oid


@pytest.mark.asyncio
async def test_transfer_resolves_open_flag(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Transfer resolves open account_disabled flag by inserting a review row."""
    admin_oid = uuid4()
    old_pi = uuid4()
    new_pi = uuid4()

    app, _sub = await _insert_app_with_submission(db_setup, pi_oid=old_pi)

    # Insert an open (unreviewed) flag
    flag = AppOwnershipFlag(
        app_id=app.id,
        pi_principal_id=old_pi,
        reason="account_disabled",
    )
    db_setup.add(flag)
    await db_setup.commit()

    with patch(
        "rac_control_plane.services.ownership.transfer._default_validate_pi_fn",
        new=AsyncMock(return_value=Ok()),
    ):
        r = await client.post(
            f"/admin/apps/{app.id}/ownership/transfer",
            json={
                "new_pi_principal_id": str(new_pi),
                "new_dept_fallback": "New Dept",
                "justification": "Resolved by new PI assignment",
            },
            headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
        )
    assert r.status_code == 200

    # Flag should now have a review row with review_decision='resolved_by_transfer'
    review_result = await db_setup.execute(
        select(AppOwnershipFlagReview).where(
            AppOwnershipFlagReview.flag_id == flag.id
        )
    )
    reviews = review_result.scalars().all()
    assert len(reviews) == 1
    assert reviews[0].review_decision == "resolved_by_transfer"
    assert reviews[0].reviewer_principal_id == admin_oid


@pytest.mark.asyncio
async def test_transfer_invalid_new_pi_returns_422(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Invalid new PI (disabled or not found) → 422 Unprocessable Entity."""
    admin_oid = uuid4()
    app, _sub = await _insert_app_with_submission(db_setup, pi_oid=uuid4())

    with patch(
        "rac_control_plane.services.ownership.transfer._default_validate_pi_fn",
        new=AsyncMock(return_value=Invalid(reason="account_disabled")),
    ):
        r = await client.post(
            f"/admin/apps/{app.id}/ownership/transfer",
            json={
                "new_pi_principal_id": str(uuid4()),
                "new_dept_fallback": "Some Dept",
                "justification": "Should fail",
            },
            headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
        )
    assert r.status_code == 422
    assert "invalid_new_pi" in r.json()["code"]


@pytest.mark.asyncio
async def test_transfer_non_admin_returns_403(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Non-admin principal cannot perform ownership transfer (403)."""
    user_oid = uuid4()
    app, _sub = await _insert_app_with_submission(db_setup, pi_oid=uuid4())

    r = await client.post(
        f"/admin/apps/{app.id}/ownership/transfer",
        json={
            "new_pi_principal_id": str(uuid4()),
            "new_dept_fallback": "Some Dept",
            "justification": "Should be forbidden",
        },
        headers={"Authorization": f"Bearer {_user_token(mock_oidc, user_oid)}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_transfer_app_not_found_returns_404(
    client,
    mock_oidc,
) -> None:
    """Transfer to a non-existent app_id → 404."""
    admin_oid = uuid4()

    with patch(
        "rac_control_plane.services.ownership.transfer._default_validate_pi_fn",
        new=AsyncMock(return_value=Ok()),
    ):
        r = await client.post(
            f"/admin/apps/{uuid4()}/ownership/transfer",
            json={
                "new_pi_principal_id": str(uuid4()),
                "new_dept_fallback": "Some Dept",
                "justification": "App does not exist",
            },
            headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
        )
    assert r.status_code == 404
