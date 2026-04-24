"""Asset request/response schemas.

Type-only module — no FCIS classification needed.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SasRequestBody(BaseModel):
    """Request body for minting a SAS token."""

    name: str
    mount_path: str
    max_size_bytes: int | None = None


class SasCredentialsResponse(BaseModel):
    """Response for SAS mint endpoint."""

    upload_url: str
    blob_path: str
    expires_at: datetime
    max_size_bytes: int


class FinalizeUploadRequest(BaseModel):
    """Request body for finalizing a direct upload."""

    name: str
    blob_path: str
    declared_sha256: str
    declared_size_bytes: int | None = None
    mount_path: str


class AssetResponse(BaseModel):
    """Response model for an asset."""

    id: UUID
    submission_id: UUID | None
    name: str | None
    kind: str
    mount_path: str | None
    blob_path: str | None
    blob_uri: str | None
    sha256: str | None
    size_bytes: int | None
    status: str
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    created_at: datetime
