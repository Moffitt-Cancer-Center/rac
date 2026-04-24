# pattern: Imperative Shell
"""Webhook HMAC secret rotation scheduled job logic.

Finds enabled WebhookSubscription rows whose secrets are older than
``rotation_days`` and generates new Key Vault secret versions.

Key Vault retains the previous version for the grace period
(``settings.webhook_secret_grace_period_hours``) before disabling it,
ensuring in-flight deliveries signed with the old secret remain valid
during the transition window.

This function is called by the ACA scheduled job endpoint at
``POST /internal/jobs/rotate-webhook-secrets``.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import WebhookSubscription

logger = structlog.get_logger(__name__)


async def rotate_expiring_secrets(
    session: AsyncSession,
    *,
    rotation_days: int,
    kv_client_factory: Callable[[], object] | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    """Rotate HMAC secrets for subscriptions past their rotation threshold.

    Args:
        session:           Async DB session.
        rotation_days:     Subscriptions not rotated within this many days
                           (or never rotated) are eligible.
        kv_client_factory: Callable returning a SecretClient. Falls back to
                           DefaultAzureCredential if None.
        now:               Override current time (for testing).

    Returns:
        List of subscription UUIDs whose secrets were rotated.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    threshold = now - timedelta(days=rotation_days)

    # Query: enabled=true AND (secret_rotated_at IS NULL OR secret_rotated_at < threshold)
    stmt = select(WebhookSubscription).where(
        WebhookSubscription.enabled.is_(True),
        (WebhookSubscription.secret_rotated_at.is_(None))
        | (WebhookSubscription.secret_rotated_at < threshold),
    )
    result = await session.execute(stmt)
    subscriptions = list(result.scalars().all())

    rotated_ids: list[UUID] = []

    for sub in subscriptions:
        new_secret_value = secrets.token_hex(32)
        try:
            await _set_kv_secret(sub.secret_name, new_secret_value, kv_client_factory)
        except Exception:
            logger.exception(
                "secret_rotation_kv_failed",
                sub_id=str(sub.id),
                secret_name=sub.secret_name,
            )
            continue

        sub.secret_rotated_at = now
        rotated_ids.append(sub.id)
        logger.info(
            "webhook_secret_rotated",
            sub_id=str(sub.id),
            secret_name=sub.secret_name,
        )

    if rotated_ids:
        await session.commit()

    return rotated_ids


async def _set_kv_secret(
    secret_name: str,
    secret_value: str,
    kv_client_factory: Callable[[], object] | None,
) -> None:
    """Write a new Key Vault secret version."""
    if kv_client_factory is not None:
        client = kv_client_factory()
        await client.set_secret(secret_name, secret_value)  # type: ignore[attr-defined]
        return

    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient

    from rac_control_plane.settings import get_settings
    settings = get_settings()

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=settings.kv_uri, credential=credential)
    try:
        await client.set_secret(secret_name, secret_value, content_type="text/plain")
    finally:
        await client.close()
        await credential.close()
