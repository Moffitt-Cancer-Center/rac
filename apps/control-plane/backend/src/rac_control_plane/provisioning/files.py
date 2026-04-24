# pattern: Imperative Shell
"""Azure Files provisioning wrapper.

Creates a per-app Azure Files share. Idempotent.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


async def ensure_app_share(
    storage_account_name: str,
    share_name: str,
    tags: dict[str, str],
    *,
    storage_client: Any = None,
) -> str:
    """Create a file share if it does not exist. Idempotent.

    Args:
        storage_account_name: Azure storage account name.
        share_name: Name for the file share (typically the app slug).
        tags: AC11.1 tags applied as share metadata.
        storage_client: Optional injected StorageManagementClient for testing.

    Returns:
        The file share resource ID.

    Raises:
        TransientProvisioningError: On 429/5xx.
        ProvisioningError: On permanent errors.
    """
    from azure.core.exceptions import HttpResponseError
    from azure.mgmt.storage import StorageManagementClient
    from azure.mgmt.storage.models import FileShare

    from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError

    settings = get_settings()

    if storage_client is None:
        from rac_control_plane.provisioning.credentials import get_azure_credential
        credential = get_azure_credential()
        storage_client = StorageManagementClient(
            credential=credential,
            subscription_id=settings.subscription_id,
        )

    try:
        result = await asyncio.to_thread(
            lambda: storage_client.file_shares.create(
                resource_group_name=settings.resource_group,
                account_name=storage_account_name,
                share_name=share_name,
                file_share=FileShare(
                    metadata=tags,
                    share_quota=100,
                ),
            )
        )
        resource_id: str = result.id or ""
        logger.info(
            "file_share_created",
            share_name=share_name,
            storage_account=storage_account_name,
        )
        return resource_id

    except HttpResponseError as exc:
        status: int = (exc.response.status_code if exc.response else None) or 0
        msg = str(exc.error.message if exc.error else exc)[:200]

        if status in (429, 500, 502, 503, 504):
            raise TransientProvisioningError(
                code="files_transient",
                detail=f"Files HTTP {status}: {msg}",
            ) from exc

        if status == 409:
            raise ProvisioningError(
                code="files_conflict",
                detail=f"Files conflict creating share {share_name}: {msg}",
                retryable=False,
            ) from exc

        if 400 <= status < 500:
            raise ProvisioningError(
                code="files_error",
                detail=f"Files error {status} for {share_name}: {msg}",
                retryable=False,
            ) from exc

        raise TransientProvisioningError(
            code="files_transient",
            detail=f"Files unexpected error {status}: {msg}",
        ) from exc
