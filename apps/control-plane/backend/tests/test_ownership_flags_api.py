"""Integration tests for GET /admin/ownership/flags.

Verifies:
- Insert 3 flags (2 open, 1 reviewed) → GET returns exactly 2.
- Non-admin principal → 403.

Verifies: rac-v1.AC9.2
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    App,
    AppOwnershipFlag,
    AppOwnershipFlagReview,
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


async def _insert_app(db: AsyncSession, *, pi_oid: UUID | None = None) -> App:
    """Insert a minimal App row (with a dependency Submission)."""
    pi = pi_oid or uuid4()
    slug = f"app-{uuid4().hex[:8]}"

    sub = Submission(
        slug=slug,
        status=SubmissionStatus.deployed,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=pi,
        dept_fallback="Test Dept",
    )
    db.add(sub)
    await db.flush()

    app = App(
        slug=slug,
        pi_principal_id=pi,
        dept_fallback="Test Dept",
        current_submission_id=sub.id,
        target_port=8000,
    )
    db.add(app)
    await db.commit()
    return app


async def _insert_flag(
    db: AsyncSession,
    *,
    app: App,
    reason: str = "account_disabled",
) -> AppOwnershipFlag:
    flag = AppOwnershipFlag(
        app_id=app.id,
        pi_principal_id=app.pi_principal_id,
        reason=reason,
    )
    db.add(flag)
    await db.commit()
    return flag


async def _review_flag(
    db: AsyncSession,
    *,
    flag: AppOwnershipFlag,
    reviewer_oid: UUID,
) -> AppOwnershipFlagReview:
    review = AppOwnershipFlagReview(
        flag_id=flag.id,
        review_decision="acknowledged",
        reviewer_principal_id=reviewer_oid,
    )
    db.add(review)
    await db.commit()
    return review


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_flags_returns_only_open(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """2 open flags + 1 reviewed flag → GET returns 2."""
    admin_oid = uuid4()

    app1 = await _insert_app(db_setup)
    app2 = await _insert_app(db_setup)
    app3 = await _insert_app(db_setup)

    flag1 = await _insert_flag(db_setup, app=app1, reason="account_disabled")
    flag2 = await _insert_flag(db_setup, app=app2, reason="not_found")
    flag3 = await _insert_flag(db_setup, app=app3, reason="account_disabled")

    # Review flag3 → it becomes closed
    await _review_flag(db_setup, flag=flag3, reviewer_oid=admin_oid)

    response = await client.get(
        "/admin/ownership/flags",
        headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)

    # The response may contain flags from other tests (db_setup doesn't rollback).
    # Assert our specific open flags are present and the reviewed flag is absent.
    returned_ids = {item["flag_id"] for item in body}
    assert str(flag1.id) in returned_ids, "flag1 (open) should be in response"
    assert str(flag2.id) in returned_ids, "flag2 (open) should be in response"
    assert str(flag3.id) not in returned_ids, "flag3 (reviewed) must NOT be in response"

    # Verify shape of a returned item
    for item in body:
        assert "flag_id" in item
        assert "app_id" in item
        assert "app_slug" in item
        assert "pi_principal_id" in item
        assert "reason" in item
        assert "flagged_at" in item
        assert item["reason"] in ("account_disabled", "not_found")


@pytest.mark.asyncio
async def test_list_flags_returns_empty_when_all_reviewed(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """All flags reviewed → the specific reviewed flag does not appear in the list."""
    admin_oid = uuid4()

    app1 = await _insert_app(db_setup)
    flag1 = await _insert_flag(db_setup, app=app1)
    await _review_flag(db_setup, flag=flag1, reviewer_oid=admin_oid)

    response = await client.get(
        "/admin/ownership/flags",
        headers={"Authorization": f"Bearer {_admin_token(mock_oidc, admin_oid)}"},
    )

    assert response.status_code == 200
    body = response.json()
    # The reviewed flag must NOT appear; other flags from other tests may exist
    returned_ids = {item["flag_id"] for item in body}
    assert str(flag1.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_flags_non_admin_returns_403(
    client,
    db_setup: AsyncSession,
    mock_oidc,
) -> None:
    """Non-admin principal receives 403."""
    user_oid = uuid4()

    response = await client.get(
        "/admin/ownership/flags",
        headers={"Authorization": f"Bearer {_user_token(mock_oidc, user_oid)}"},
    )

    assert response.status_code == 403
