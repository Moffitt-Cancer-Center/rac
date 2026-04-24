"""Tests for webhook subscription admin CRUD (Task 9).

Scenarios:
1. admin creates sub → 201 with {id, secret}; secret not returned on GET
2. non-admin POST → 403
3. PATCH toggles enabled → row reflects change
4. DELETE removes row
"""

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from rac_control_plane.data.models import WebhookSubscription


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin_token(mock_oidc: Any) -> str:
    """Issue a user token with IT-approver (admin) role."""
    return mock_oidc.issue_user_token(
        oid=uuid4(),
        roles=["it_approver"],
    )


def _user_token(mock_oidc: Any) -> str:
    """Issue a regular user token without admin role."""
    return mock_oidc.issue_user_token(oid=uuid4(), roles=[])


_CREATE_BODY = {
    "name": "Test Subscriber",
    "callback_url": "https://subscriber.example.org/hook",
    "event_types": ["submission.scan_completed"],
}


async def _create_sub(client: AsyncClient, mock_oidc: Any) -> dict[str, Any]:
    """Helper to create a subscription and return the parsed JSON body."""
    token = _admin_token(mock_oidc)
    with patch(
        "rac_control_plane.api.routes.webhook_subscriptions._store_secret_in_kv",
        new=AsyncMock(),
    ):
        resp = await client.post(
            "/admin/webhook-subscriptions",
            json=_CREATE_BODY,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Scenario 1: admin creates sub → 201 with id + secret; GET has no secret
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_create_returns_secret_once(
    client: AsyncClient, mock_oidc: Any
) -> None:
    """POST /admin/webhook-subscriptions → 201 with one-shot secret; GET returns no secret."""
    data = await _create_sub(client, mock_oidc)

    assert "id" in data, f"Missing id in response: {data}"
    assert "secret" in data, f"Missing secret in response: {data}"
    assert len(data["secret"]) == 64, "Secret should be 64 hex chars (32 bytes)"
    assert data["name"] == _CREATE_BODY["name"]
    assert data["callback_url"] == _CREATE_BODY["callback_url"]
    assert data["event_types"] == _CREATE_BODY["event_types"]
    assert data["enabled"] is True
    assert data["consecutive_failures"] == 0

    # GET detail — secret must NOT appear
    token = _admin_token(mock_oidc)
    sub_id = data["id"]
    detail_resp = await client.get(
        f"/admin/webhook-subscriptions/{sub_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert "secret" not in detail, f"Secret should not appear in GET response: {detail}"
    assert detail["id"] == sub_id


# ---------------------------------------------------------------------------
# Scenario 2: non-admin POST → 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_forbidden(client: AsyncClient, mock_oidc: Any) -> None:
    """POST /admin/webhook-subscriptions without admin role → 403."""
    token = _user_token(mock_oidc)
    resp = await client.post(
        "/admin/webhook-subscriptions",
        json=_CREATE_BODY,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Scenario 3: PATCH toggles enabled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_toggle_enabled(
    client: AsyncClient, mock_oidc: Any, db_setup: Any
) -> None:
    """PATCH /admin/webhook-subscriptions/{id} with enabled=false disables the subscription."""
    data = await _create_sub(client, mock_oidc)
    sub_id = data["id"]

    token = _admin_token(mock_oidc)
    resp = await client.patch(
        f"/admin/webhook-subscriptions/{sub_id}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # Verify in DB
    from uuid import UUID
    result = await db_setup.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == UUID(sub_id)
        )
    )
    ws = result.scalar_one_or_none()
    assert ws is not None
    assert ws.enabled is False


# ---------------------------------------------------------------------------
# Scenario 4: DELETE removes row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_removes_row(
    client: AsyncClient, mock_oidc: Any, db_setup: Any
) -> None:
    """DELETE /admin/webhook-subscriptions/{id} → 204; row no longer in DB."""
    data = await _create_sub(client, mock_oidc)
    sub_id = data["id"]

    token = _admin_token(mock_oidc)
    resp = await client.delete(
        f"/admin/webhook-subscriptions/{sub_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204

    from uuid import UUID
    result = await db_setup.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == UUID(sub_id)
        )
    )
    ws = result.scalar_one_or_none()
    assert ws is None, "Subscription row should be deleted"
