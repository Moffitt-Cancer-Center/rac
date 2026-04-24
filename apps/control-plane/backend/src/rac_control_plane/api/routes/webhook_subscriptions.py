# pattern: Imperative Shell
"""Admin CRUD endpoints for webhook subscriptions.

All endpoints require the admin role (``require_admin`` dependency).

POST   /admin/webhook-subscriptions        — create; returns one-shot secret
GET    /admin/webhook-subscriptions        — list all
GET    /admin/webhook-subscriptions/{id}  — detail (no secret)
PATCH  /admin/webhook-subscriptions/{id}  — update
DELETE /admin/webhook-subscriptions/{id}  — remove row (KV secret lingers)
"""

import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.webhook_subscriptions import (
    WebhookSubscriptionCreate,
    WebhookSubscriptionCreateResponse,
    WebhookSubscriptionResponse,
    WebhookSubscriptionUpdate,
)
from rac_control_plane.auth.dependencies import require_admin
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import WebhookSubscription
from rac_control_plane.errors import NotFoundError
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/webhook-subscriptions", tags=["admin", "webhooks"])


async def _store_secret_in_kv(secret_name: str, secret_value: str) -> None:
    """Store HMAC secret in Azure Key Vault."""
    settings = get_settings()
    try:
        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.kv_uri, credential=credential)
        try:
            await client.set_secret(secret_name, secret_value, content_type="text/plain")
        finally:
            await client.close()
            await credential.close()
    except Exception:
        logger.exception("kv_store_secret_failed", secret_name=secret_name)
        raise


@router.post("", status_code=201)
async def create_subscription(
    body: WebhookSubscriptionCreate,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> WebhookSubscriptionCreateResponse:
    """Create a webhook subscription.

    Returns the plaintext HMAC secret exactly once — store it immediately.
    """
    secret_value = secrets.token_hex(32)  # 64 hex chars = 32 bytes
    secret_name = f"rac-webhook-hmac-{secrets.token_hex(8)}"

    # Persist secret to KV
    await _store_secret_in_kv(secret_name, secret_value)

    sub = WebhookSubscription(
        name=body.name,
        callback_url=body.callback_url,
        event_types=body.event_types,
        secret_name=secret_name,
        enabled=True,
        consecutive_failures=0,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)

    logger.info("webhook_subscription_created", sub_id=str(sub.id), name=body.name)

    return WebhookSubscriptionCreateResponse(
        id=sub.id,
        name=sub.name,
        callback_url=sub.callback_url,
        event_types=list(sub.event_types),
        enabled=sub.enabled,
        consecutive_failures=sub.consecutive_failures,
        last_delivery_at=sub.last_delivery_at,
        secret_rotated_at=sub.secret_rotated_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
        secret=secret_value,
    )


@router.get("", status_code=200)
async def list_subscriptions(
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> list[WebhookSubscriptionResponse]:
    """List all webhook subscriptions."""
    result = await session.execute(select(WebhookSubscription))
    subs = result.scalars().all()
    return [
        WebhookSubscriptionResponse(
            id=s.id,
            name=s.name,
            callback_url=s.callback_url,
            event_types=list(s.event_types),
            enabled=s.enabled,
            consecutive_failures=s.consecutive_failures,
            last_delivery_at=s.last_delivery_at,
            secret_rotated_at=s.secret_rotated_at,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in subs
    ]


@router.get("/{sub_id}", status_code=200)
async def get_subscription(
    sub_id: UUID,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> WebhookSubscriptionResponse:
    """Get a single webhook subscription (secret not returned)."""
    sub = await _get_or_404(session, sub_id)
    return WebhookSubscriptionResponse(
        id=sub.id,
        name=sub.name,
        callback_url=sub.callback_url,
        event_types=list(sub.event_types),
        enabled=sub.enabled,
        consecutive_failures=sub.consecutive_failures,
        last_delivery_at=sub.last_delivery_at,
        secret_rotated_at=sub.secret_rotated_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )


@router.patch("/{sub_id}", status_code=200)
async def update_subscription(
    sub_id: UUID,
    body: WebhookSubscriptionUpdate,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> WebhookSubscriptionResponse:
    """Update a webhook subscription."""
    sub = await _get_or_404(session, sub_id)

    if body.name is not None:
        sub.name = body.name
    if body.callback_url is not None:
        sub.callback_url = body.callback_url
    if body.event_types is not None:
        sub.event_types = body.event_types
    if body.enabled is not None:
        sub.enabled = body.enabled
    if body.reset_failures:
        sub.consecutive_failures = 0

    sub.updated_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(sub)

    return WebhookSubscriptionResponse(
        id=sub.id,
        name=sub.name,
        callback_url=sub.callback_url,
        event_types=list(sub.event_types),
        enabled=sub.enabled,
        consecutive_failures=sub.consecutive_failures,
        last_delivery_at=sub.last_delivery_at,
        secret_rotated_at=sub.secret_rotated_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )


@router.delete("/{sub_id}", status_code=204)
async def delete_subscription(
    sub_id: UUID,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delete a webhook subscription row.

    The Key Vault HMAC secret is NOT deleted — it lingers until its natural
    expiry. This is intentional: KV version history is preserved for audit.
    """
    sub = await _get_or_404(session, sub_id)
    await session.delete(sub)
    await session.commit()
    logger.info("webhook_subscription_deleted", sub_id=str(sub_id))
    return Response(status_code=204)


async def _get_or_404(session: AsyncSession, sub_id: UUID) -> WebhookSubscription:
    """Load a WebhookSubscription or raise NotFoundError."""
    stmt = select(WebhookSubscription).where(WebhookSubscription.id == sub_id)
    result = await session.execute(stmt)
    sub = result.scalar_one_or_none()
    if sub is None:
        raise NotFoundError(f"Webhook subscription {sub_id} not found")
    return sub
