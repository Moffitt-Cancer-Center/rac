# pattern: Imperative Shell
"""Finalize a direct researcher upload to Azure Blob Storage.

After the browser uploads a file via SAS, the client calls /finalize.
This service re-downloads the blob, recomputes sha256, and verifies
the declared hash before inserting the asset row.

This design ensures server-side integrity even if the client lies about
the hash or size.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import Asset
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.assets.sha256_stream import astream_sha256

logger = structlog.get_logger(__name__)


def _safe_delete_blob(blob_client: Any, asset_name: str) -> None:
    """Idempotent blob delete: swallow exceptions from an already-gone blob or
    concurrent delete. We still raise the caller's integrity error afterward;
    the blob cleanup is best-effort. Prevents a retried /finalize from 500'ing
    when the first call already deleted the mismatching blob."""
    try:
        blob_client.delete_blob()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "delete_blob_failed_during_mismatch_cleanup",
            asset_name=asset_name,
            error=str(exc),
        )


async def finalize_upload(
    session: AsyncSession,
    *,
    submission_id: UUID,
    asset_name: str,
    blob_path: str,
    declared_sha256: str,
    declared_size_bytes: int | None,
    mount_path: str,
    blob_client_factory: Callable[..., Any] | None = None,  # for test injection
    account_url: str | None = None,
    container_name: str = "researcher-uploads",
    dispatch_fn: Callable[..., Any] | None = None,  # forwarded to finalize_submission
) -> Asset:
    """Verify a researcher-uploaded blob and insert an asset row.

    Steps:
    1. Open BlobClient (or use injected factory); stream-download the blob
       while computing sha256 via astream_sha256.
    2. Compare computed sha256 to declared_sha256 (case-insensitive).
    3. If declared_size_bytes provided, verify it matches actual size.
    4. Mismatch → delete blob; raise ValidationApiError(code='sha256_mismatch').
    5. Match → INSERT asset row with status='ready'; return it.

    Args:
        session: Active async session (caller owns commit).
        submission_id: Submission UUID that owns this asset.
        asset_name: Logical asset name (e.g. "reference-genome").
        blob_path: Relative blob path (e.g. "submissions/<id>/genome.fa").
        declared_sha256: Researcher-declared sha256 hex digest.
        declared_size_bytes: Declared file size (optional; additional integrity check).
        mount_path: Absolute container path for the asset mount.
        blob_client_factory: Optional factory callable(account_url, container, blob) → BlobClient.
        account_url: Blob storage account URL (ignored if blob_client_factory provided).
        container_name: Container holding the upload (default: researcher-uploads).

    Returns:
        Inserted Asset ORM row (status='ready').

    Raises:
        ValidationApiError: code='sha256_mismatch' or 'size_mismatch' on integrity failure.
    """
    # Build the BlobClient
    blob_client: Any
    if blob_client_factory is not None:
        blob_client = blob_client_factory(account_url, container_name, blob_path)
    else:
        if account_url is None:
            from rac_control_plane.settings import get_settings
            account_url = get_settings().blob_account_url

        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        blob_client = BlobClient(
            account_url=account_url,
            container_name=container_name,
            blob_name=blob_path,
            credential=DefaultAzureCredential(),
        )

    # Stream the blob and compute sha256
    download_stream = blob_client.download_blob()
    computed_sha256, computed_size = await astream_sha256(
        _iter_blob_chunks(download_stream)
    )

    # Verify sha256 (case-insensitive — hex digits only)
    if computed_sha256.lower() != declared_sha256.lower():
        _safe_delete_blob(blob_client, asset_name)
        raise ValidationApiError(
            code="sha256_mismatch",
            public_message=(
                f"sha256 mismatch for asset '{asset_name}': "
                f"declared {declared_sha256!r}, computed {computed_sha256!r}"
            ),
        )

    # Verify size (optional cross-check)
    if declared_size_bytes is not None and computed_size != declared_size_bytes:
        _safe_delete_blob(blob_client, asset_name)
        raise ValidationApiError(
            code="size_mismatch",
            public_message=(
                f"size mismatch for asset '{asset_name}': "
                f"declared {declared_size_bytes}, computed {computed_size}"
            ),
        )

    # Construct the canonical blob URI (without SAS — the server has managed identity access)
    if account_url is None:
        from rac_control_plane.settings import get_settings
        account_url = get_settings().blob_account_url
    _account_url = account_url
    blob_uri = f"{_account_url.rstrip('/')}/{container_name}/{blob_path}"

    # Insert the asset row
    asset = Asset(
        submission_id=submission_id,
        name=asset_name,
        kind="upload",
        mount_path=mount_path,
        blob_path=blob_path,
        blob_uri=blob_uri,
        sha256=computed_sha256,
        size_bytes=computed_size,
        status="ready",
    )
    session.add(asset)
    await session.flush()

    # Signal-trigger finalization: check if this upload unblocks the pipeline
    from rac_control_plane.services.submissions.finalize import finalize_submission
    await finalize_submission(session, submission_id, dispatch_fn=dispatch_fn)

    return asset


async def _iter_blob_chunks(download_stream: Any) -> AsyncIterator[bytes]:
    """Async generator: yield chunks from a BlobDownloadClient.

    The Azure SDK's download_blob() returns a StorageStreamDownloader.
    Its chunks() method returns a sync iterator; we wrap it for use with
    astream_sha256 (which expects an async iterator).
    """
    for chunk in download_stream.chunks():
        yield chunk
