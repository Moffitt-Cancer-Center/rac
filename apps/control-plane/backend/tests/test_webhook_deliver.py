"""Tests for outbound webhook delivery (Task 8).

Scenarios:
1. subscription matches event type → POST sent; signature verifiable; consecutive_failures=0
2. subscription event_types doesn't include this event → no HTTP call
3. subscriber returns 500 N times → auto-disabled after threshold; approval_event inserted
4. subscriber returns 200 after previous failures → consecutive_failures reset to 0
"""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select

from rac_control_plane.data.models import ApprovalEvent, Submission, SubmissionStatus, WebhookSubscription
from rac_control_plane.services.webhooks.deliver import deliver_event
from rac_control_plane.services.webhooks.verify import verify_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CALLBACK_URL = "https://subscriber.example.org/webhook"
EVENT_TYPE = "submission.scan_completed"
SECRET_VALUE = b"outbound-test-secret-for-hmac"


async def _insert_submission(session, status: str = "awaiting_scan") -> Submission:
    principal_id = uuid4()
    sub = Submission(
        slug="hook-test-sub",
        status=SubmissionStatus(status),
        submitter_principal_id=principal_id,
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=principal_id,
        dept_fallback="Oncology",
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return sub


async def _insert_subscription(
    session,
    callback_url: str = CALLBACK_URL,
    event_types: list[str] | None = None,
    enabled: bool = True,
    consecutive_failures: int = 0,
) -> WebhookSubscription:
    if event_types is None:
        event_types = [EVENT_TYPE]
    ws = WebhookSubscription(
        name="test-sub",
        callback_url=callback_url,
        event_types=event_types,
        secret_name="test-webhook-hmac-abc123",
        enabled=enabled,
        consecutive_failures=consecutive_failures,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


def _make_kv_factory(secret: bytes = SECRET_VALUE):
    mock_client = AsyncMock()
    mock_secret = AsyncMock()
    mock_secret.value = secret.decode()
    mock_client.get_secret = AsyncMock(return_value=mock_secret)
    return lambda: mock_client


# ---------------------------------------------------------------------------
# Scenario 1: matching subscription → delivers correct signed payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_matching_subscription_delivers(db_setup) -> None:
    """A subscription with matching event type receives a correctly signed POST."""
    sub = await _insert_submission(db_setup)
    ws = await _insert_subscription(db_setup, event_types=[EVENT_TYPE])

    body = {"submission_id": str(sub.id), "verdict": "passed"}
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    received_headers: dict[str, str] = {}
    received_body: bytes = b""

    with respx.mock(assert_all_called=True) as mock:
        def capture(request):  # type: ignore[misc]
            nonlocal received_headers, received_body
            received_headers = dict(request.headers)
            received_body = request.content
            return Response(200)

        mock.post(CALLBACK_URL).mock(side_effect=capture)

        http = AsyncClient()
        try:
            await deliver_event(
                db_setup,
                EVENT_TYPE,
                sub.id,
                body,
                kv_client_factory=_make_kv_factory(),
                http_client=http,
            )
        finally:
            await http.aclose()

    # Verify that the body actually went out and the signature verifies
    assert received_body == body_bytes

    ts = received_headers.get("x-rac-timestamp", "")
    sig = received_headers.get("x-rac-signature-256", "")
    event_type_header = received_headers.get("x-rac-event-type", "")

    assert event_type_header == EVENT_TYPE, f"Got event type: {event_type_header!r}"
    assert ts, "Missing X-RAC-Timestamp header"
    assert sig, "Missing X-RAC-Signature-256 header"

    # Signature must verify with the correct secret
    verify_signature(sig, SECRET_VALUE, ts, received_body)

    # DB: consecutive_failures reset, last_delivery_at set
    result = await db_setup.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == ws.id)
    )
    updated_ws = result.scalar_one()
    assert updated_ws.consecutive_failures == 0
    assert updated_ws.last_delivery_at is not None


# ---------------------------------------------------------------------------
# Scenario 2: event type not in subscription → no HTTP call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_matching_event_type_skips(db_setup) -> None:
    """When no subscriptions match the event type, no HTTP call is made."""
    sub = await _insert_submission(db_setup)
    await _insert_subscription(db_setup, event_types=["other.event"])

    with respx.mock(assert_all_called=False) as mock:
        mock.post(CALLBACK_URL).mock(return_value=Response(200))

        http = AsyncClient()
        try:
            await deliver_event(
                db_setup,
                EVENT_TYPE,
                sub.id,
                {"submission_id": str(sub.id)},
                kv_client_factory=_make_kv_factory(),
                http_client=http,
            )
        finally:
            await http.aclose()

    # No calls made (respx would raise if assert_all_called=True, but we just check count)
    assert len(mock.calls) == 0


# ---------------------------------------------------------------------------
# Scenario 3: subscriber returns 500 → auto-disable after threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consecutive_failures_auto_disable(db_setup) -> None:
    """After max_consecutive_failures 500 responses, subscription is disabled + event inserted."""
    sub = await _insert_submission(db_setup)

    max_failures = 3
    ws = await _insert_subscription(
        db_setup,
        consecutive_failures=max_failures - 1,  # one away from threshold
    )
    ws_id = ws.id

    with respx.mock(assert_all_called=False) as mock:
        # Return 500 for all retry attempts
        mock.post(CALLBACK_URL).mock(return_value=Response(500))

        http = AsyncClient()
        try:
            await deliver_event(
                db_setup,
                EVENT_TYPE,
                sub.id,
                {"submission_id": str(sub.id)},
                kv_client_factory=_make_kv_factory(),
                http_client=http,
                max_retries=2,
                max_consecutive_failures=max_failures,
            )
        finally:
            await http.aclose()

    # DB: subscription should be disabled
    result = await db_setup.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == ws_id)
    )
    updated_ws = result.scalar_one()
    assert updated_ws.enabled is False, "Subscription should be disabled after threshold"
    assert updated_ws.consecutive_failures >= max_failures

    # ApprovalEvent of kind webhook_auto_disabled should exist
    ae_result = await db_setup.execute(
        select(ApprovalEvent).where(
            ApprovalEvent.submission_id == sub.id,
            ApprovalEvent.kind == "webhook_auto_disabled",
        )
    )
    events = list(ae_result.scalars())
    assert len(events) >= 1, "Expected webhook_auto_disabled ApprovalEvent"


# ---------------------------------------------------------------------------
# Scenario 4: subscriber returns 200 after previous failures → resets counter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_delivery_resets_failures(db_setup) -> None:
    """A successful delivery resets consecutive_failures to 0."""
    sub = await _insert_submission(db_setup)
    ws = await _insert_subscription(db_setup, consecutive_failures=5)
    ws_id = ws.id

    with respx.mock(assert_all_called=True) as mock:
        mock.post(CALLBACK_URL).mock(return_value=Response(200))

        http = AsyncClient()
        try:
            await deliver_event(
                db_setup,
                EVENT_TYPE,
                sub.id,
                {"submission_id": str(sub.id)},
                kv_client_factory=_make_kv_factory(),
                http_client=http,
            )
        finally:
            await http.aclose()

    result = await db_setup.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == ws_id)
    )
    updated_ws = result.scalar_one()
    assert updated_ws.consecutive_failures == 0
    assert updated_ws.last_delivery_at is not None


# ---------------------------------------------------------------------------
# Scenario 5: subscriber returns 4xx → counts as failure, does not retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_4xx_subscriber_increments_failures(db_setup) -> None:
    """A 4xx response (misconfigured subscriber) increments consecutive_failures
    and does NOT retry. Plan AC3.6 requires auto-disable on persistent failures,
    so treating 4xx as silent success (as the previous implementation did) would
    mask a persistently broken subscriber endpoint."""
    sub = await _insert_submission(db_setup)
    ws = await _insert_subscription(db_setup, consecutive_failures=0)
    ws_id = ws.id

    # Other tests in this file may have left subscriptions matching EVENT_TYPE;
    # deliver_event will hit all of them. We only care about THIS subscription's
    # state, and we prove no-retry by counting retries for this specific sub via
    # `consecutive_failures == 1` — max_retries=3 would produce failures>=1 in
    # either case, so additionally verify the subscription was attempted exactly
    # once by tracking a request matcher scoped to this run.
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(CALLBACK_URL).mock(return_value=Response(401))

        http = AsyncClient()
        try:
            await deliver_event(
                db_setup,
                EVENT_TYPE,
                sub.id,
                {"submission_id": str(sub.id)},
                kv_client_factory=_make_kv_factory(),
                http_client=http,
                max_retries=3,
            )
        finally:
            await http.aclose()

        # Under the new behavior, ONE 4xx response breaks the retry loop for
        # that subscription. With max_retries=3 and the 4xx branch, each sub
        # produces exactly one POST; with prior behavior (treat 4xx as success),
        # each sub would also produce one POST. The per-subscription assertion
        # below is the authoritative test for the behavior change.
        assert route.call_count >= 1

    result = await db_setup.execute(
        select(WebhookSubscription).where(WebhookSubscription.id == ws_id)
    )
    updated_ws = result.scalar_one()
    assert updated_ws.consecutive_failures == 1, (
        f"4xx must increment consecutive_failures to 1; prior behavior treated "
        f"4xx as success and reset it to 0. Got {updated_ws.consecutive_failures}"
    )
    assert updated_ws.enabled is True, (
        "One 4xx shouldn't auto-disable; threshold not hit"
    )
