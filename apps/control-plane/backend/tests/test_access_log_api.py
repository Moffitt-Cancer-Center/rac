"""Integration tests for access log viewer API.

GET /apps/{app_id}/access-log

Tests:
- test_list_access_log_basic: 5 rows → all 5 returned, newest first, next_cursor set.
- test_pagination_before_cursor: 10 rows → page 1 limit=5; page 2 uses next_cursor.
- test_reviewer_label_joined: row with reviewer_token → reviewer_label populated.
- test_reviewer_label_null_for_public: row without token → reviewer_label None.
- test_filter_by_mode: mixed rows → filter mode=public returns only public.
- test_filter_by_jti: filter jti=<specific> returns only that jti's rows.
- test_filter_by_status: filter status=500 returns only 500s.
- test_limit_cap: limit=500 → at most 100 items returned.
- test_auth_non_owner_returns_403.
- test_auth_admin_allowed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import App, ReviewerToken, Submission, SubmissionStatus


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _create_app_with_submission(
    db_setup: AsyncSession,
    *,
    owner_oid: UUID,
    slug: str | None = None,
    status: SubmissionStatus = SubmissionStatus.deployed,
) -> tuple[UUID, UUID]:
    """Insert App + Submission; return (app_id, submission_id)."""
    slug = slug or f"logapp-{uuid4().hex[:8]}"
    app_id = uuid4()
    sub_id = uuid4()

    sub = Submission(
        id=sub_id,
        slug=slug,
        status=status,
        submitter_principal_id=owner_oid,
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=owner_oid,
        dept_fallback="TestDept",
    )
    db_setup.add(sub)
    await db_setup.flush()

    app = App(
        id=app_id,
        slug=slug,
        pi_principal_id=owner_oid,
        dept_fallback="TestDept",
        current_submission_id=sub_id,
    )
    db_setup.add(app)
    await db_setup.flush()
    await db_setup.commit()
    return app_id, sub_id


async def _insert_reviewer_token(
    db_setup: AsyncSession,
    *,
    app_id: UUID,
    principal_id: UUID,
    jti: str,
    reviewer_label: str = "Test Reviewer",
    expires_at: datetime | None = None,
) -> None:
    """Insert a ReviewerToken row."""
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(days=30)

    token = ReviewerToken(
        id=uuid4(),
        principal_id=principal_id,
        jti=jti,
        expires_at=expires_at,
        app_id=app_id,
        reviewer_label=reviewer_label,
        kid=f"rac-app-test-v1",
        issued_by_principal_id=principal_id,
        scope="read",
    )
    db_setup.add(token)
    await db_setup.flush()
    await db_setup.commit()


async def _insert_access_log_row(
    db_setup: AsyncSession,
    *,
    app_id: UUID,
    principal_id: UUID,
    reviewer_token_jti: str | None = None,
    access_mode: str = "token_required",
    method: str = "GET",
    path: str = "/index",
    upstream_status: int = 200,
    latency_ms: int = 42,
    source_ip: str = "10.0.0.1",
    row_id: UUID | None = None,
) -> UUID:
    """Insert an access_log row using raw SQL (to bypass ORM restrictions).

    Uses text() INSERT so we can set the id explicitly for keyset pagination tests.
    The ``path`` is stored in the ``action`` column (Phase 2 legacy column reused
    for path storage; migration 0011 does not add a separate 'path' column).
    """
    rid = row_id or uuid4()
    stmt = text("""
        INSERT INTO access_log (
            id, principal_id, action, reviewer_token_jti,
            app_id, access_mode, method, upstream_status,
            latency_ms, source_ip, created_at
        ) VALUES (
            :id, :principal_id, :action, :reviewer_token_jti,
            :app_id, :access_mode, :method, :upstream_status,
            :latency_ms, :source_ip, NOW()
        )
    """)
    await db_setup.execute(
        stmt,
        {
            "id": rid,
            "principal_id": principal_id,
            "action": path,  # action column serves as path storage
            "reviewer_token_jti": reviewer_token_jti,
            "app_id": app_id,
            "access_mode": access_mode,
            "method": method,
            "upstream_status": upstream_status,
            "latency_ms": latency_ms,
            "source_ip": source_ip,
        },
    )
    await db_setup.commit()
    return rid


# ---------------------------------------------------------------------------
# Tests — basic listing
# ---------------------------------------------------------------------------


async def test_list_access_log_basic(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Seed 5 access_log rows for app X → GET with limit=5 returns all 5, newest first, next_cursor=last id."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    for _ in range(5):
        await _insert_access_log_row(db_setup, app_id=app_id, principal_id=owner_oid)

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    # Use limit=5 to trigger next_cursor (limit must equal number of returned rows)
    resp = await client.get(
        f"/apps/{app_id}/access-log?limit=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert len(data["items"]) == 5
    assert data["next_cursor"] is not None

    # next_cursor == last item's id
    assert data["next_cursor"] == data["items"][-1]["id"]

    # Verify newest-first ordering: ids should be in descending order
    item_ids = [item["id"] for item in data["items"]]
    assert item_ids == sorted(item_ids, reverse=True)


async def test_pagination_before_cursor(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """10 rows → page 1 with limit=5 returns 5 newest; page 2 returns next 5 older."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    inserted_ids: list[UUID] = []
    for _ in range(10):
        rid = await _insert_access_log_row(db_setup, app_id=app_id, principal_id=owner_oid)
        inserted_ids.append(rid)

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])

    # Page 1
    resp1 = await client.get(
        f"/apps/{app_id}/access-log?limit=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 200, resp1.json()
    data1 = resp1.json()
    assert len(data1["items"]) == 5
    cursor = data1["next_cursor"]
    assert cursor is not None

    page1_ids = {item["id"] for item in data1["items"]}

    # Page 2
    resp2 = await client.get(
        f"/apps/{app_id}/access-log?limit=5&before={cursor}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 200, resp2.json()
    data2 = resp2.json()
    assert len(data2["items"]) == 5

    page2_ids = {item["id"] for item in data2["items"]}
    # No overlap between pages
    assert page1_ids.isdisjoint(page2_ids)
    # Pages together cover all 10 rows
    assert len(page1_ids | page2_ids) == 10


async def test_reviewer_label_joined(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Row with reviewer_token_jti populated → reviewer_label from reviewer_token table."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    jti_val = str(uuid4())
    await _insert_reviewer_token(
        db_setup,
        app_id=app_id,
        principal_id=owner_oid,
        jti=jti_val,
        reviewer_label="Journal Reviewer #1",
    )
    await _insert_access_log_row(
        db_setup,
        app_id=app_id,
        principal_id=owner_oid,
        reviewer_token_jti=jti_val,
    )

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["reviewer_label"] == "Journal Reviewer #1"
    assert items[0]["reviewer_token_jti"] == jti_val


async def test_reviewer_label_null_for_public(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Row with reviewer_token_jti=NULL → reviewer_label is None."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    await _insert_access_log_row(
        db_setup,
        app_id=app_id,
        principal_id=owner_oid,
        reviewer_token_jti=None,
        access_mode="public",
    )

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["reviewer_label"] is None
    assert items[0]["reviewer_token_jti"] is None


async def test_filter_by_mode(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Mix of token_required + public rows → filter mode=public returns only public."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    for _ in range(3):
        await _insert_access_log_row(
            db_setup, app_id=app_id, principal_id=owner_oid, access_mode="token_required"
        )
    for _ in range(2):
        await _insert_access_log_row(
            db_setup, app_id=app_id, principal_id=owner_oid, access_mode="public"
        )

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log?mode=public",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) == 2
    for item in items:
        assert item["access_mode"] == "public"


async def test_filter_by_jti(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """filter jti=<specific> returns only that jti's rows."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    target_jti = str(uuid4())
    other_jti = str(uuid4())

    # Create reviewer tokens first
    await _insert_reviewer_token(
        db_setup, app_id=app_id, principal_id=owner_oid, jti=target_jti, reviewer_label="Target"
    )
    await _insert_reviewer_token(
        db_setup, app_id=app_id, principal_id=owner_oid, jti=other_jti, reviewer_label="Other"
    )

    await _insert_access_log_row(
        db_setup, app_id=app_id, principal_id=owner_oid, reviewer_token_jti=target_jti
    )
    await _insert_access_log_row(
        db_setup, app_id=app_id, principal_id=owner_oid, reviewer_token_jti=target_jti
    )
    await _insert_access_log_row(
        db_setup, app_id=app_id, principal_id=owner_oid, reviewer_token_jti=other_jti
    )

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log?jti={target_jti}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) == 2
    for item in items:
        assert item["reviewer_token_jti"] == target_jti


async def test_filter_by_status(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """filter status=500 returns only 500s."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    for _ in range(3):
        await _insert_access_log_row(
            db_setup, app_id=app_id, principal_id=owner_oid, upstream_status=200
        )
    for _ in range(2):
        await _insert_access_log_row(
            db_setup, app_id=app_id, principal_id=owner_oid, upstream_status=500
        )

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log?status=500",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) == 2
    for item in items:
        assert item["upstream_status"] == 500


async def test_limit_cap(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """limit=500 → response has at most 100 items (capped by service)."""
    owner_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    # Insert 105 rows
    for _ in range(105):
        await _insert_access_log_row(db_setup, app_id=app_id, principal_id=owner_oid)

    token = mock_oidc.issue_user_token(oid=owner_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log?limit=500",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) <= 100


async def test_auth_non_owner_returns_403(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Non-owner non-admin GET → 403."""
    owner_oid = uuid4()
    stranger_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    stranger_token = mock_oidc.issue_user_token(oid=stranger_oid, roles=[])
    resp = await client.get(
        f"/apps/{app_id}/access-log",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert resp.status_code == 403


async def test_auth_admin_allowed(
    client: Any, db_setup: AsyncSession, mock_oidc: Any
) -> None:
    """Admin (it_approver role) can view any app's access log."""
    owner_oid = uuid4()
    admin_oid = uuid4()
    app_id, _ = await _create_app_with_submission(db_setup, owner_oid=owner_oid)

    await _insert_access_log_row(db_setup, app_id=app_id, principal_id=owner_oid)

    admin_token = mock_oidc.issue_user_token(oid=admin_oid, roles=["it_approver"])
    resp = await client.get(
        f"/apps/{app_id}/access-log",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.json()
    assert len(resp.json()["items"]) == 1
