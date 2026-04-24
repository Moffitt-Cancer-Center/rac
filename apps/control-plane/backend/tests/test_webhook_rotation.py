"""Tests for HMAC secret rotation job (Task 9B).

Scenarios:
1. 3 subscriptions: 2 old/null + 1 recent → only 2 rotated
2. New-secret delivery validates with verify_signature
3. Endpoint auth: no X-Internal-Auth header → 404; correct header → 200
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from rac_control_plane.data.models import WebhookSubscription
from rac_control_plane.services.webhooks.rotate_secrets import rotate_expiring_secrets
from rac_control_plane.services.webhooks.sign import sign_payload
from rac_control_plane.services.webhooks.verify import verify_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _insert_sub(
    session,
    secret_rotated_at: datetime | None,
    enabled: bool = True,
) -> WebhookSubscription:
    ws = WebhookSubscription(
        name=f"sub-{uuid4()}",
        callback_url="https://example.org/hook",
        event_types=["submission.scan_completed"],
        secret_name=f"rac-webhook-hmac-{uuid4().hex[:8]}",
        enabled=enabled,
        consecutive_failures=0,
        secret_rotated_at=secret_rotated_at,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


def _make_kv_factory() -> tuple[Any, dict[str, str]]:
    """Return (factory, stored_secrets) where stored_secrets tracks KV writes."""
    stored: dict[str, str] = {}

    mock_client = AsyncMock()

    async def _set_secret(name: str, value: str) -> None:
        stored[name] = value

    mock_client.set_secret = _set_secret
    return lambda: mock_client, stored


# ---------------------------------------------------------------------------
# Scenario 1: 2 old + 1 recent → only 2 rotated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotation_only_affects_old_subscriptions(db_setup) -> None:
    """rotate_expiring_secrets rotates aged/null subscriptions, not recent ones."""
    now = datetime.now(tz=UTC)
    rotation_days = 30
    threshold = now - timedelta(days=rotation_days)

    # Sub 1: secret_rotated_at = NULL (never rotated)
    sub1 = await _insert_sub(db_setup, secret_rotated_at=None)

    # Sub 2: rotated 40 days ago (past threshold)
    sub2 = await _insert_sub(
        db_setup, secret_rotated_at=now - timedelta(days=40)
    )

    # Sub 3: rotated 5 days ago (recent, should be skipped)
    sub3_rotated = now - timedelta(days=5)
    sub3 = await _insert_sub(
        db_setup, secret_rotated_at=sub3_rotated
    )

    factory, stored = _make_kv_factory()

    rotated = await rotate_expiring_secrets(
        db_setup,
        rotation_days=rotation_days,
        kv_client_factory=factory,
        now=now,
    )

    # Only sub1 and sub2 should be in the returned list (other test subs may appear too)
    rotated_set = set(rotated)
    assert sub1.id in rotated_set, "sub1 (NULL rotated_at) should be rotated"
    assert sub2.id in rotated_set, "sub2 (40 days old) should be rotated"
    assert sub3.id not in rotated_set, "sub3 (5 days old) should NOT be rotated"

    # Verify KV was called for the 2 aged subscriptions by their specific secret_name
    assert sub1.secret_name in stored, f"KV not written for {sub1.secret_name}"
    assert sub2.secret_name in stored, f"KV not written for {sub2.secret_name}"
    assert sub3.secret_name not in stored, f"KV incorrectly written for {sub3.secret_name}"

    # Verify secret_rotated_at was updated in DB for sub1
    r1 = await db_setup.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == sub1.id)
    )
    updated1 = r1.scalar_one()
    assert updated1.secret_rotated_at is not None
    assert abs((updated1.secret_rotated_at - now).total_seconds()) < 5

    # Verify sub3 was NOT updated (still has its original rotated_at)
    r3 = await db_setup.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == sub3.id)
    )
    updated3 = r3.scalar_one()
    assert updated3.secret_rotated_at is not None
    # sub3 was rotated 5 days ago — still within 30-day threshold
    assert abs((updated3.secret_rotated_at - sub3_rotated).total_seconds()) < 5


# ---------------------------------------------------------------------------
# Scenario 2: delivery after rotation validates with the new secret
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_secret_verifies_delivery(db_setup) -> None:
    """After rotation, the new secret value validates a signed payload."""
    now = datetime.now(tz=UTC)

    sub = await _insert_sub(db_setup, secret_rotated_at=None)

    factory, stored = _make_kv_factory()

    await rotate_expiring_secrets(
        db_setup,
        rotation_days=30,
        kv_client_factory=factory,
        now=now,
    )

    # The new secret is now in `stored[sub.secret_name]`
    new_secret_hex = stored[sub.secret_name]
    assert len(new_secret_hex) == 64, "Rotated secret should be 64 hex chars"

    new_secret_bytes = new_secret_hex.encode()

    # Sign a payload with the new secret and verify it
    body = b'{"event":"test"}'
    ts, sig = sign_payload(new_secret_bytes, body)

    # This should NOT raise SignatureInvalid
    verify_signature(sig, new_secret_bytes, ts, body)


# ---------------------------------------------------------------------------
# Scenario 3a: /internal/jobs/rotate-webhook-secrets without header → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotate_endpoint_no_auth_returns_404(
    client: AsyncClient, mock_oidc: Any
) -> None:
    """POST /internal/jobs/rotate-webhook-secrets without X-Internal-Auth → 404."""
    resp = await client.post("/internal/jobs/rotate-webhook-secrets")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Scenario 3b: correct X-Internal-Auth → 200 with rotated list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotate_endpoint_with_correct_auth(
    client: AsyncClient, mock_oidc: Any, db_session
) -> None:
    """POST /internal/jobs/rotate-webhook-secrets with correct header → 200."""
    import os
    from unittest.mock import patch

    secret_value = "super-secret-job-key"

    with patch.dict(os.environ, {"RAC_INTERNAL_JOB_SECRET": secret_value}):
        from rac_control_plane.settings import get_settings
        get_settings.cache_clear()

        # Patch the rotate function to avoid DB interaction in this test
        from unittest.mock import AsyncMock as AM
        with patch(
            "rac_control_plane.api.routes.jobs.rotate_expiring_secrets",
            new=AM(return_value=[]),
        ):
            resp = await client.post(
                "/internal/jobs/rotate-webhook-secrets",
                headers={"X-Internal-Auth": secret_value},
            )

        get_settings.cache_clear()

    assert resp.status_code == 200
    data = resp.json()
    assert "rotated" in data
    assert isinstance(data["rotated"], list)
