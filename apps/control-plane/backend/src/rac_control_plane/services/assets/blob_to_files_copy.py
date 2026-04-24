# pattern: Imperative Shell
"""Blob → Azure Files copy at deploy time.

At app provisioning time, this service copies each ready asset from the
researcher-uploads Blob container into the per-app Azure Files share.
ACA mounts the share into the container at each asset's declared mount_path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import App, Asset, Submission


async def populate_app_share_from_assets(
    session: AsyncSession,
    *,
    app: App,
    submission: Submission,
    storage_account_name: str,
    share_name: str,
    blob_client_factory: Callable[..., Any] | None = None,   # for test injection
    share_client_factory: Callable[..., Any] | None = None,  # for test injection
) -> list[str]:
    """Copy all ready assets from Blob to the Azure Files share.

    For each ready asset of the submission:
    1. Download blob content (streaming).
    2. Upload to the Azure Files share at path <asset.name>.
    3. Set file metadata: sha256, asset_id, source (kind).

    Returns the list of asset names copied.

    Args:
        session: Active async session.
        app: The App row being provisioned.
        submission: The Submission whose assets to copy.
        storage_account_name: Storage account hosting both Blob and Files.
        share_name: Azure Files share name (typically the app slug).
        blob_client_factory: Optional factory for BlobClient injection.
        share_client_factory: Optional factory for ShareFileClient injection.

    Returns:
        List of asset names copied to the share.
    """
    # Load all ready assets for this submission
    result = await session.execute(
        select(Asset).where(
            Asset.submission_id == submission.id,
            Asset.status == "ready",
        )
    )
    assets = list(result.scalars().all())

    if not assets:
        return []

    copied: list[str] = []
    for asset in assets:
        await _copy_asset_to_share(
            asset=asset,
            storage_account_name=storage_account_name,
            share_name=share_name,
            blob_client_factory=blob_client_factory,
            share_client_factory=share_client_factory,
        )
        copied.append(asset.name or "")

    return copied


async def _copy_asset_to_share(
    *,
    asset: Asset,
    storage_account_name: str,
    share_name: str,
    blob_client_factory: Callable[..., Any] | None,
    share_client_factory: Callable[..., Any] | None,
) -> None:
    """Copy a single asset blob to the Azure Files share."""
    import asyncio

    # Determine the target file name in the share (use asset.name as the filename)
    file_name = asset.name or str(asset.id)

    # Build BlobClient
    blob_client = _make_blob_client(
        factory=blob_client_factory,
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        container_name="researcher-uploads",
        blob_path=asset.blob_path or f"submissions/{asset.submission_id}/{file_name}",
    )

    # Download blob content (sync SDK wrapped in thread)
    data: bytes = await asyncio.to_thread(_download_blob_sync, blob_client)

    # Build ShareFileClient
    share_file_client = _make_share_file_client(
        factory=share_client_factory,
        account_name=storage_account_name,
        share_name=share_name,
        file_name=file_name,
    )

    # Upload to Azure Files share (sync SDK wrapped in thread)
    metadata = {
        "sha256": asset.sha256 or "",
        "asset_id": str(asset.id),
        "source": asset.kind or "upload",
    }
    await asyncio.to_thread(_upload_to_share_sync, share_file_client, data, metadata)


def _download_blob_sync(blob_client: Any) -> bytes:
    """Synchronously download blob content."""
    stream = blob_client.download_blob()
    result: bytes = stream.readall()
    return result


def _upload_to_share_sync(
    share_file_client: Any,
    data: bytes,
    metadata: dict[str, str],
) -> None:
    """Synchronously upload bytes to an Azure Files share file."""
    import io

    share_file_client.upload_file(io.BytesIO(data))
    # Set metadata on the file (sha256, asset_id, source kind)
    share_file_client.set_file_metadata(metadata=metadata)


def _make_blob_client(
    *,
    factory: Callable[..., Any] | None,
    account_url: str,
    container_name: str,
    blob_path: str,
) -> Any:
    if factory is not None:
        return factory(account_url, container_name, blob_path)

    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    return BlobClient(
        account_url=account_url,
        container_name=container_name,
        blob_name=blob_path,
        credential=DefaultAzureCredential(),
    )


def _make_share_file_client(
    *,
    factory: Callable[..., Any] | None,
    account_name: str,
    share_name: str,
    file_name: str,
) -> Any:
    if factory is not None:
        return factory(account_name, share_name, file_name)

    from azure.identity import DefaultAzureCredential
    from azure.storage.fileshare import ShareFileClient  # type: ignore[import-untyped]

    account_url = f"https://{account_name}.file.core.windows.net"
    return ShareFileClient(
        account_url=account_url,
        share_name=share_name,
        file_path=file_name,
        credential=DefaultAzureCredential(),
    )
