# pattern: Imperative Shell
"""Azure Key Vault key provisioning wrapper.

Creates an ES256 (P-256) signing key for per-app JWT signing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class KeyIdentifier:
    """Identifies a specific version of a Key Vault key."""

    kid: str       # full Key Vault key URI including version
    key_name: str  # logical key name (e.g. 'rac-app-myapp-v1')
    version: str   # key version string


async def create_signing_key(
    app_slug: str,
    tags: dict[str, str],
    *,
    key_client: Any = None,
) -> KeyIdentifier:
    """Create an ES256 (P-256) signing key in Key Vault. Idempotent.

    Key name: rac-app-<slug>-v1.

    Args:
        app_slug: Application slug.
        tags: AC11.1 tags applied to the key.
        key_client: Optional injected KeyClient for testing.

    Returns:
        KeyIdentifier with kid, key_name, version.

    Raises:
        TransientProvisioningError: On 429/5xx.
        ProvisioningError: On permanent errors.
    """
    from azure.core.exceptions import HttpResponseError
    from azure.keyvault.keys import KeyClient, KeyCurveName

    from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError

    settings = get_settings()
    key_name = f"rac-app-{app_slug}-v1"

    if key_client is None:
        from rac_control_plane.provisioning.credentials import get_azure_credential
        credential = get_azure_credential()
        key_client = KeyClient(
            vault_url=settings.kv_uri,
            credential=credential,
        )

    try:
        result = await asyncio.to_thread(
            lambda: key_client.create_ec_key(
                name=key_name,
                curve=KeyCurveName.p_256,
                key_operations=["sign", "verify"],
                tags=tags,
            )
        )
        kid: str = result.id or ""
        version: str = result.properties.version or ""
        logger.info("signing_key_created", key_name=key_name, version=version)
        return KeyIdentifier(kid=kid, key_name=key_name, version=version)

    except HttpResponseError as exc:
        status: int = (exc.response.status_code if exc.response else None) or 0
        msg = str(exc.error.message if exc.error else exc)[:200]

        if status in (429, 500, 502, 503, 504):
            raise TransientProvisioningError(
                code="kv_transient",
                detail=f"KeyVault HTTP {status}: {msg}",
            ) from exc

        if status == 409:
            raise ProvisioningError(
                code="kv_conflict",
                detail=f"KeyVault conflict creating {key_name}: {msg}",
                retryable=False,
            ) from exc

        if 400 <= status < 500:
            raise ProvisioningError(
                code="kv_error",
                detail=f"KeyVault error {status} for {key_name}: {msg}",
                retryable=False,
            ) from exc

        raise TransientProvisioningError(
            code="kv_transient",
            detail=f"KeyVault unexpected error {status}: {msg}",
        ) from exc
