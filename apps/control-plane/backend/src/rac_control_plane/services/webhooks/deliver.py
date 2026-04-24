# pattern: Imperative Shell
"""Outbound webhook delivery with retries and auto-disable.

Queries enabled WebhookSubscription rows whose ``event_types`` JSON array
contains the requested event type, fetches each subscription's HMAC secret
from Key Vault, signs the payload, and POSTs to the subscriber's callback URL.

Retry policy: exponential backoff (1 s base, cap 30 s), up to ``max_retries``
attempts.  On permanent failure the subscription's ``consecutive_failures``
counter is incremented; once it reaches ``max_consecutive_failures`` the
subscription is disabled and an ``ApprovalEvent`` of kind
``webhook_auto_disabled`` is inserted for visibility in admin views.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import ApprovalEvent, WebhookSubscription
from rac_control_plane.services.webhooks.sign import sign_payload

logger = structlog.get_logger(__name__)


async def deliver_event(
    session: AsyncSession,
    event_type: str,
    submission_id: UUID,
    body: dict,  # type: ignore[type-arg]
    *,
    kv_client_factory: Callable[[], object] | None = None,
    http_client: httpx.AsyncClient | None = None,
    max_retries: int = 5,
    max_consecutive_failures: int = 10,
) -> None:
    """Deliver an event to all matching enabled webhook subscriptions.

    Args:
        session:                  Async DB session (will be committed here).
        event_type:               Event type string (e.g. ``"submission.scan_completed"``).
        submission_id:            UUID of the submission the event relates to.
        body:                     Event payload dict; serialised to canonical JSON.
        kv_client_factory:        Callable returning an Azure SecretClient.  When
                                  None, a DefaultAzureCredential-backed client is
                                  used (requires Azure SDK available).
        http_client:              Override httpx client for testing.
        max_retries:              Maximum delivery attempts per subscription.
        max_consecutive_failures: Threshold at which a subscription is auto-disabled.
    """
    # Query matching subscriptions
    stmt = select(WebhookSubscription).where(
        WebhookSubscription.enabled.is_(True),
        WebhookSubscription.event_types.contains([event_type]),
    )
    result = await session.execute(stmt)
    subscriptions = list(result.scalars().all())

    if not subscriptions:
        return

    # Canonical JSON body (sorted keys, no whitespace) — sign these exact bytes
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    # Build http client (reuse if provided — tests inject respx)
    own_client = http_client is None
    _http: httpx.AsyncClient = (
        http_client if http_client is not None else httpx.AsyncClient(timeout=30.0)
    )

    try:
        for sub in subscriptions:
            await _deliver_to_subscription(
                session=session,
                sub=sub,
                event_type=event_type,
                body_bytes=body_bytes,
                kv_client_factory=kv_client_factory,
                http_client=_http,
                max_retries=max_retries,
                max_consecutive_failures=max_consecutive_failures,
                submission_id=submission_id,
            )
    finally:
        if own_client:
            await _http.aclose()

    await session.commit()


async def _fetch_secret(secret_name: str, kv_client_factory: Callable[[], object] | None) -> bytes:
    """Fetch HMAC secret bytes from Key Vault."""
    if kv_client_factory is not None:
        client = kv_client_factory()
        secret = await client.get_secret(secret_name)  # type: ignore[attr-defined]
        return secret.value.encode()  # type: ignore[no-any-return]

    # Real Key Vault via DefaultAzureCredential
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient

    from rac_control_plane.settings import get_settings
    settings = get_settings()
    credential = DefaultAzureCredential()
    kv = SecretClient(vault_url=settings.kv_uri, credential=credential)
    try:
        secret = await kv.get_secret(secret_name)
        return secret.value.encode() if secret.value else b""
    finally:
        await kv.close()
        await credential.close()


async def _deliver_to_subscription(
    *,
    session: AsyncSession,
    sub: WebhookSubscription,
    event_type: str,
    body_bytes: bytes,
    kv_client_factory: Callable[[], object] | None,
    http_client: httpx.AsyncClient,
    max_retries: int,
    max_consecutive_failures: int,
    submission_id: UUID,
) -> None:
    """Attempt delivery to a single subscription with retries."""
    try:
        secret_bytes = await _fetch_secret(sub.secret_name, kv_client_factory)
    except Exception:
        logger.exception("webhook_secret_fetch_failed", sub_id=str(sub.id))
        _increment_failures(session, sub, max_consecutive_failures, submission_id)
        return

    timestamp, signature = sign_payload(secret_bytes, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-RAC-Event-Type": event_type,
        "X-RAC-Timestamp": timestamp,
        "X-RAC-Signature-256": signature,
    }

    success = False
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = await http_client.post(
                sub.callback_url,
                content=body_bytes,
                headers=headers,
            )
            if 200 <= resp.status_code < 300:
                success = True
                break
            if 400 <= resp.status_code < 500:
                # 4xx — subscriber-side error (misconfigured URL, auth, bad payload).
                # Don't retry; count as a failure so persistently broken endpoints auto-disable.
                break
            # 5xx — retry with backoff
        except (httpx.HTTPError, OSError):
            pass  # network error — retry

        if attempt < max_retries - 1:
            await asyncio.sleep(min(delay, 30.0))
            delay *= 2

    now = datetime.now(tz=UTC)
    if success:
        sub.consecutive_failures = 0
        sub.last_delivery_at = now
        logger.info(
            "webhook_delivered",
            sub_id=str(sub.id),
            event_type=event_type,
        )
    else:
        _increment_failures(session, sub, max_consecutive_failures, submission_id)


def _increment_failures(
    session: AsyncSession,
    sub: WebhookSubscription,
    max_consecutive_failures: int,
    submission_id: UUID,
) -> None:
    """Increment failure counter; auto-disable if threshold reached."""
    sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
    logger.warning(
        "webhook_delivery_failed",
        sub_id=str(sub.id),
        consecutive_failures=sub.consecutive_failures,
    )

    if sub.consecutive_failures >= max_consecutive_failures:
        sub.enabled = False
        logger.error(
            "webhook_auto_disabled",
            sub_id=str(sub.id),
            consecutive_failures=sub.consecutive_failures,
        )
        # Insert audit event visible in admin views
        audit_event = ApprovalEvent(
            submission_id=submission_id,
            kind="webhook_auto_disabled",
            actor_principal_id=None,
            payload={
                "webhook_subscription_id": str(sub.id),
                "consecutive_failures": sub.consecutive_failures,
            },
        )
        session.add(audit_event)
