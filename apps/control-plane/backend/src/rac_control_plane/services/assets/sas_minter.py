# pattern: Imperative Shell
"""SAS credential minting for direct-to-Blob researcher uploads.

Mints a user-delegation SAS on researcher-uploads/submissions/<id>/<name>
with add+write+create permissions (no read). The browser client uses the
resulting URL to PUT the file directly to Azure Blob Storage, bypassing
FastAPI workers (which would bottleneck on bandwidth).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class SasCredentials:
    """SAS token response for a browser direct-upload."""

    upload_url: str       # blob URL + SAS query string
    blob_path: str        # relative path (e.g. "submissions/<uuid>/<name>")
    expires_at: datetime  # UTC datetime
    max_size_bytes: int


async def mint_upload_sas(
    submission_id: UUID,
    asset_name: str,
    *,
    account_url: str,
    container_name: str = "researcher-uploads",
    max_size_bytes: int = 5 * 1024 * 1024 * 1024,  # 5 GB default
    max_age_seconds: int = 3600,
    credential: Any = None,  # TokenCredential or None — DefaultAzureCredential used when None
    now: datetime | None = None,
) -> SasCredentials:
    """Mint a user-delegation SAS for a researcher upload.

    Uses BlobServiceClient.get_user_delegation_key() so the SAS is tied to the
    managed identity's delegated authority (not a storage account key).

    Permissions: add=True, write=True, create=True. No read — the researcher
    can upload but cannot download; the server re-downloads for verification.

    Args:
        submission_id: The submission UUID (used to scope the blob path).
        asset_name: Logical name of the asset (used as blob filename).
        account_url: Azure Blob Storage account URL.
        container_name: Container name (default: researcher-uploads).
        max_size_bytes: Maximum allowed file size (informational; enforced at finalize).
        max_age_seconds: SAS expiry window in seconds (default: 1 hour).
        credential: Azure credential (DefaultAzureCredential if None).
        now: Override current time (for testing).

    Returns:
        SasCredentials with upload_url, blob_path, expires_at, max_size_bytes.
    """
    from azure.storage.blob import (
        BlobSasPermissions,
        BlobServiceClient,
        generate_blob_sas,
    )

    if now is None:
        now = datetime.now(UTC)

    if credential is None:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()

    blob_path = f"submissions/{submission_id}/{asset_name}"
    expiry = now + timedelta(seconds=max_age_seconds)

    # Fetch a user-delegation key valid for the SAS window
    service_client = BlobServiceClient(account_url=account_url, credential=credential)
    user_delegation_key = service_client.get_user_delegation_key(
        key_start_time=now,
        key_expiry_time=expiry,
    )

    # Extract storage account name from the account URL
    # account_url is like "https://<account>.blob.core.windows.net/"
    account_name = account_url.split("//")[1].split(".")[0]

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_path,
        user_delegation_key=user_delegation_key,
        permission=BlobSasPermissions(add=True, write=True, create=True),
        expiry=expiry,
    )

    upload_url = (
        f"{account_url.rstrip('/')}/{container_name}/{blob_path}?{sas_token}"
    )

    return SasCredentials(
        upload_url=upload_url,
        blob_path=blob_path,
        expires_at=expiry,
        max_size_bytes=max_size_bytes,
    )
