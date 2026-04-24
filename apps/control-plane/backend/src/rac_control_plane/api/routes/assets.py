# pattern: Imperative Shell
"""Asset management API routes.

Endpoints:
- POST /submissions/{submission_id}/assets/uploads/sas
    Mint a SAS token for direct-to-Blob researcher upload.
    Auth: submitter or admin.

- POST /submissions/{submission_id}/assets/uploads/finalize
    Verify uploaded blob and insert asset row.
    Auth: submitter or admin.

- GET /submissions/{submission_id}/assets
    List all assets for a submission.
    Auth: submitter, approver, or admin.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.assets import (
    AssetResponse,
    FinalizeUploadRequest,
    SasCredentialsResponse,
    SasRequestBody,
)
from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import Asset, Submission
from rac_control_plane.data.submission_repo import get_by_id
from rac_control_plane.errors import ForbiddenError, NotFoundError
from rac_control_plane.services.assets.sas_minter import mint_upload_sas
from rac_control_plane.services.assets.upload import finalize_upload
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/submissions", tags=["assets"])

_DEFAULT_MAX_SIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB


async def _get_submission_or_404(
    session: AsyncSession, submission_id: UUID
) -> Submission:
    """Fetch Submission by id or raise NotFoundError."""
    sub = await get_by_id(session, submission_id)
    if sub is None:
        raise NotFoundError(public_message="Submission not found")
    return sub


def _require_submitter_or_admin(
    principal: Principal,
    submission: Submission,
    settings: object,
) -> None:
    """Raise ForbiddenError unless the principal is the submitter or an admin.

    Admin = holds the it_approver role.
    """
    from rac_control_plane.settings import Settings

    if not isinstance(settings, Settings):
        raise TypeError("expected Settings instance")

    is_admin = settings.approver_role_it in principal.roles
    is_submitter = principal.oid == submission.submitter_principal_id
    if not (is_admin or is_submitter):
        raise ForbiddenError(public_message="Not the submitter or admin for this submission")


def _require_read_access(
    principal: Principal,
    submission: Submission,
    settings: object,
) -> None:
    """Allow submitter, any approver role, or admin."""
    from rac_control_plane.settings import Settings

    if not isinstance(settings, Settings):
        raise TypeError("expected Settings instance")

    is_submitter = principal.oid == submission.submitter_principal_id
    is_approver = (
        settings.approver_role_research in principal.roles
        or settings.approver_role_it in principal.roles
    )
    if not (is_submitter or is_approver):
        raise ForbiddenError(
            public_message="Not authorised to view assets for this submission"
        )


def _asset_to_response(asset: Asset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        submission_id=asset.submission_id,
        name=asset.name,
        kind=asset.kind,
        mount_path=asset.mount_path,
        blob_path=asset.blob_path,
        blob_uri=asset.blob_uri,
        sha256=asset.sha256,
        size_bytes=asset.size_bytes,
        status=asset.status,
        expected_sha256=asset.expected_sha256,
        actual_sha256=asset.actual_sha256,
        created_at=asset.created_at,
    )


@router.post(
    "/{submission_id}/assets/uploads/sas",
    response_model=SasCredentialsResponse,
    status_code=201,
)
async def mint_sas(
    submission_id: UUID,
    body: SasRequestBody,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SasCredentialsResponse:
    """Mint a SAS token for a direct researcher upload to Blob Storage.

    The browser uses the returned upload_url to PUT the file directly to
    Azure Blob Storage (no data flows through the Control Plane workers).
    After upload completes, call /finalize to verify and register the asset.
    """
    settings = get_settings()
    submission = await _get_submission_or_404(session, submission_id)
    _require_submitter_or_admin(principal, submission, settings)

    max_size = body.max_size_bytes or _DEFAULT_MAX_SIZE_BYTES

    credentials = await mint_upload_sas(
        submission_id=submission_id,
        asset_name=body.name,
        account_url=settings.blob_account_url,
        container_name=settings.researcher_uploads_container_name,
        max_size_bytes=max_size,
    )

    logger.info(
        "sas_minted",
        submission_id=str(submission_id),
        asset_name=body.name,
        expires_at=credentials.expires_at.isoformat(),
    )

    return SasCredentialsResponse(
        upload_url=credentials.upload_url,
        blob_path=credentials.blob_path,
        expires_at=credentials.expires_at,
        max_size_bytes=credentials.max_size_bytes,
    )


@router.post(
    "/{submission_id}/assets/uploads/finalize",
    response_model=AssetResponse,
    status_code=201,
)
async def finalize_asset_upload(
    submission_id: UUID,
    body: FinalizeUploadRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AssetResponse:
    """Finalize a direct researcher upload by verifying the sha256.

    The server re-downloads the blob, recomputes sha256, and on match inserts
    an asset row (status='ready'). On mismatch the blob is deleted and 422 is
    returned (the server is the ground truth even if the client lied).
    """
    settings = get_settings()
    submission = await _get_submission_or_404(session, submission_id)
    _require_submitter_or_admin(principal, submission, settings)

    asset = await finalize_upload(
        session,
        submission_id=submission_id,
        asset_name=body.name,
        blob_path=body.blob_path,
        declared_sha256=body.declared_sha256,
        declared_size_bytes=body.declared_size_bytes,
        mount_path=body.mount_path,
        account_url=settings.blob_account_url,
        container_name=settings.researcher_uploads_container_name,
    )
    await session.commit()

    logger.info(
        "upload_finalized",
        submission_id=str(submission_id),
        asset_id=str(asset.id),
        asset_name=body.name,
        sha256=asset.sha256,
    )

    return _asset_to_response(asset)


@router.get(
    "/{submission_id}/assets",
    response_model=list[AssetResponse],
)
async def list_assets(
    submission_id: UUID,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AssetResponse]:
    """List all assets for a submission.

    Accessible by the submitter, any approver role, or admin.
    """
    settings = get_settings()
    submission = await _get_submission_or_404(session, submission_id)
    _require_read_access(principal, submission, settings)

    result = await session.execute(
        select(Asset)
        .where(Asset.submission_id == submission_id)
        .order_by(Asset.created_at)
    )
    assets = list(result.scalars().all())
    return [_asset_to_response(a) for a in assets]
