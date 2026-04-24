"""Tests for the upload flow: SAS minting + blob finalize.

Verifies AC8.1: upload asset provided via submission form is stored in
Blob, sha256 is computed server-side and persisted, and asset row inserted.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import Asset, Submission, SubmissionStatus
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.assets.sas_minter import SasCredentials, mint_upload_sas
from rac_control_plane.services.assets.upload import finalize_upload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_BYTES = b"hello, world! this is a test asset payload."
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_BYTES)


def _make_blob_client_factory(
    data: bytes,
    *,
    should_raise_on_delete: bool = False,
) -> tuple[Any, MagicMock]:
    """Return (factory, mock_blob_client) pair.

    The factory produces a MagicMock BlobClient whose download_blob().chunks()
    yields `data` in a single chunk, and whose delete_blob() can be inspected.
    """
    mock_client = MagicMock()

    # download_blob().chunks() → sync iterator over chunks
    download_mock = MagicMock()
    download_mock.chunks.return_value = iter([data])
    mock_client.download_blob.return_value = download_mock

    if should_raise_on_delete:
        mock_client.delete_blob.side_effect = Exception("delete failed")

    def factory(account_url: str, container: str, blob_path: str) -> MagicMock:
        return mock_client

    return factory, mock_client


# ---------------------------------------------------------------------------
# test_mint_sas_returns_credentials
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mint_sas_returns_credentials() -> None:
    """mint_upload_sas returns valid SasCredentials with expected fields."""
    submission_id = uuid4()
    asset_name = "genome.fa"
    account_url = "https://teststorage.blob.core.windows.net/"
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Mock the Azure SDK calls
    mock_user_delegation_key = MagicMock()

    mock_service_client = MagicMock()
    mock_service_client.get_user_delegation_key.return_value = mock_user_delegation_key

    # The sas_minter imports lazily inside the async function, so we patch
    # the azure.storage.blob module-level names.
    with (
        patch(
            "azure.storage.blob.BlobServiceClient",
            return_value=mock_service_client,
        ),
        patch(
            "azure.storage.blob.generate_blob_sas",
            return_value="sig=abc123&se=2026-04-23T13%3A00%3A00Z&sp=awc",
        ) as mock_gen_sas,
        patch(
            "azure.storage.blob.BlobSasPermissions",
        ) as mock_perm_cls,
    ):
        mock_perm = MagicMock()
        mock_perm.add = True
        mock_perm.write = True
        mock_perm.create = True
        mock_perm.read = False
        mock_perm_cls.return_value = mock_perm
        creds = await mint_upload_sas(
            submission_id=submission_id,
            asset_name=asset_name,
            account_url=account_url,
            container_name="researcher-uploads",
            max_size_bytes=100 * 1024 * 1024,
            max_age_seconds=3600,
            credential=MagicMock(),
            now=now,
        )

    assert isinstance(creds, SasCredentials)
    assert "sig=" in creds.upload_url
    assert asset_name in creds.blob_path
    assert str(submission_id) in creds.blob_path
    assert creds.max_size_bytes == 100 * 1024 * 1024
    assert creds.expires_at > now

    # Verify BlobSasPermissions was constructed with correct flags
    mock_perm_cls.assert_called_once()
    call_kwargs = mock_perm_cls.call_args.kwargs
    assert call_kwargs.get("add") is True
    assert call_kwargs.get("write") is True
    assert call_kwargs.get("create") is True
    # read should not be True
    assert not call_kwargs.get("read", False)


# ---------------------------------------------------------------------------
# test_finalize_match
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_match(db_session: AsyncSession) -> None:
    """finalize_upload: matching sha256 → asset row inserted with status='ready'."""
    submission_id = uuid4()

    # Pre-insert a submission row so the FK constraint is satisfied
    from rac_control_plane.data.models import Submission
    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_session.add(sub)
    await db_session.flush()

    factory, mock_client = _make_blob_client_factory(FIXTURE_BYTES)

    asset = await finalize_upload(
        db_session,
        submission_id=submission_id,
        asset_name="genome.fa",
        blob_path=f"submissions/{submission_id}/genome.fa",
        declared_sha256=FIXTURE_SHA256,
        declared_size_bytes=FIXTURE_SIZE,
        mount_path="/mnt/data/genome.fa",
        blob_client_factory=factory,
        account_url="https://teststorage.blob.core.windows.net/",
    )

    assert asset.status == "ready"
    assert asset.sha256 == FIXTURE_SHA256
    assert asset.size_bytes == FIXTURE_SIZE
    assert asset.submission_id == submission_id
    assert asset.name == "genome.fa"
    assert asset.kind == "upload"
    assert asset.mount_path == "/mnt/data/genome.fa"
    # delete_blob must NOT have been called
    mock_client.delete_blob.assert_not_called()


# ---------------------------------------------------------------------------
# test_finalize_mismatch_deletes_blob_and_raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_mismatch_deletes_blob_and_raises(
    db_session: AsyncSession,
) -> None:
    """finalize_upload: sha256 mismatch → delete_blob called, ValidationApiError raised, no row."""
    submission_id = uuid4()
    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_session.add(sub)
    await db_session.flush()

    factory, mock_client = _make_blob_client_factory(FIXTURE_BYTES)

    bad_sha = "a" * 64  # deliberately wrong

    with pytest.raises(ValidationApiError) as exc_info:
        await finalize_upload(
            db_session,
            submission_id=submission_id,
            asset_name="genome.fa",
            blob_path=f"submissions/{submission_id}/genome.fa",
            declared_sha256=bad_sha,
            declared_size_bytes=None,
            mount_path="/mnt/data/genome.fa",
            blob_client_factory=factory,
            account_url="https://teststorage.blob.core.windows.net/",
        )

    assert exc_info.value.code == "sha256_mismatch"
    mock_client.delete_blob.assert_called_once()

    # Verify no Asset row was inserted
    from sqlalchemy import select
    result = await db_session.execute(
        select(Asset).where(Asset.submission_id == submission_id)
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# test_finalize_size_mismatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_size_mismatch(db_session: AsyncSession) -> None:
    """finalize_upload: declared_size_bytes differs → size_mismatch error, blob deleted."""
    submission_id = uuid4()
    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_session.add(sub)
    await db_session.flush()

    factory, mock_client = _make_blob_client_factory(FIXTURE_BYTES)

    with pytest.raises(ValidationApiError) as exc_info:
        await finalize_upload(
            db_session,
            submission_id=submission_id,
            asset_name="genome.fa",
            blob_path=f"submissions/{submission_id}/genome.fa",
            declared_sha256=FIXTURE_SHA256,
            declared_size_bytes=FIXTURE_SIZE + 1,  # deliberately wrong
            mount_path="/mnt/data/genome.fa",
            blob_client_factory=factory,
            account_url="https://teststorage.blob.core.windows.net/",
        )

    assert exc_info.value.code == "size_mismatch"
    mock_client.delete_blob.assert_called_once()


# ---------------------------------------------------------------------------
# test_finalize_case_insensitive_sha256
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_case_insensitive_sha256(db_session: AsyncSession) -> None:
    """finalize_upload: declared sha256 in uppercase is accepted."""
    submission_id = uuid4()
    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_session.add(sub)
    await db_session.flush()

    factory, mock_client = _make_blob_client_factory(FIXTURE_BYTES)

    asset = await finalize_upload(
        db_session,
        submission_id=submission_id,
        asset_name="data.csv",
        blob_path=f"submissions/{submission_id}/data.csv",
        declared_sha256=FIXTURE_SHA256.upper(),  # uppercase
        declared_size_bytes=None,
        mount_path="/mnt/data/data.csv",
        blob_client_factory=factory,
        account_url="https://teststorage.blob.core.windows.net/",
    )

    assert asset.status == "ready"
    mock_client.delete_blob.assert_not_called()


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_submitter_403_on_mint(client: Any, db_setup: AsyncSession) -> None:
    """Non-submitter cannot mint a SAS for a submission they don't own."""
    from tests.fixtures.oidc import mock_oidc as _mock_oidc_unused  # noqa: F401

    # Create submission owned by a different user
    submission_owner_oid = uuid4()
    requesting_user_oid = uuid4()
    submission_id = uuid4()

    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=submission_owner_oid,
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_setup.add(sub)
    await db_setup.commit()

    # Issue token as a different user (no admin role)
    from rac_control_plane.settings import get_settings
    import jwt as pyjwt

    token = pyjwt.encode(
        {"oid": str(requesting_user_oid), "roles": [], "name": "Other User"},
        "test-secret",
        algorithm="HS256",
    )

    response = await client.post(
        f"/submissions/{submission_id}/assets/uploads/sas",
        json={"name": "genome.fa", "mount_path": "/mnt/data/genome.fa"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_non_submitter_403_on_finalize(client: Any, db_setup: AsyncSession) -> None:
    """Non-submitter cannot finalize an upload for a submission they don't own."""
    submission_owner_oid = uuid4()
    requesting_user_oid = uuid4()
    submission_id = uuid4()

    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=submission_owner_oid,
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_setup.add(sub)
    await db_setup.commit()

    import jwt as pyjwt

    token = pyjwt.encode(
        {"oid": str(requesting_user_oid), "roles": [], "name": "Other User"},
        "test-secret",
        algorithm="HS256",
    )

    response = await client.post(
        f"/submissions/{submission_id}/assets/uploads/finalize",
        json={
            "name": "genome.fa",
            "blob_path": f"submissions/{submission_id}/genome.fa",
            "declared_sha256": "a" * 64,
            "mount_path": "/mnt/data/genome.fa",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_assets_returns_empty_for_new_submission(
    client: Any, db_setup: AsyncSession
) -> None:
    """GET /submissions/{id}/assets returns empty list for a new submission."""
    owner_oid = uuid4()
    submission_id = uuid4()

    sub = Submission(
        id=submission_id,
        slug=f"test-{submission_id.hex[:8]}",
        submitter_principal_id=owner_oid,
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_setup.add(sub)
    await db_setup.commit()

    import jwt as pyjwt

    token = pyjwt.encode(
        {"oid": str(owner_oid), "roles": [], "name": "Owner"},
        "test-secret",
        algorithm="HS256",
    )

    response = await client.get(
        f"/submissions/{submission_id}/assets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == []
