"""Tests for services/assets/blob_to_files_copy.py.

Verifies:
- populate_app_share_from_assets copies all ready assets (skips non-ready).
- File metadata (sha256, asset_id, source) is set on each copied file.
- Returns list of copied asset names.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import App, Asset, Submission
from rac_control_plane.services.assets.blob_to_files_copy import (
    populate_app_share_from_assets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STORAGE_ACCOUNT = "stracdev"
_SHARE_NAME = "test-app"


async def _insert_submission(session: AsyncSession) -> Submission:
    """Insert and flush a minimal Submission row."""
    sub = Submission(
        slug=f"test-{uuid4().hex[:8]}",
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    session.add(sub)
    await session.flush()
    return sub


async def _insert_app(session: AsyncSession) -> App:
    """Insert and flush a minimal App row."""
    app = App(
        slug=f"app-{uuid4().hex[:8]}",
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
    )
    session.add(app)
    await session.flush()
    return app


def _make_blob_factory(content: bytes = b"asset content") -> tuple[object, list[MagicMock]]:
    """Return (factory, list_of_clients) where each client call is tracked."""
    clients: list[MagicMock] = []

    def factory(account_url: str, container: str, blob_path: str) -> MagicMock:
        client = MagicMock()
        # download_blob().readall() returns the content
        download = MagicMock()
        download.readall.return_value = content
        client.download_blob.return_value = download
        clients.append(client)
        return client

    return factory, clients


def _make_share_factory() -> tuple[object, list[MagicMock]]:
    """Return (factory, list_of_share_clients)."""
    clients: list[MagicMock] = []

    def factory(account_name: str, share_name: str, file_name: str) -> MagicMock:
        client = MagicMock()
        clients.append(client)
        return client

    return factory, clients


# ---------------------------------------------------------------------------
# test_populate_copies_all_ready_assets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_populate_copies_all_ready_assets(db_session: AsyncSession) -> None:
    """All ready assets are copied; pending asset is skipped."""
    sub = await _insert_submission(db_session)
    app = await _insert_app(db_session)

    # Insert 2 ready assets + 1 pending
    ready1 = Asset(
        submission_id=sub.id,
        name="genome.fa",
        kind="upload",
        mount_path="/mnt/ref/genome.fa",
        blob_path=f"submissions/{sub.id}/genome.fa",
        sha256="a" * 64,
        size_bytes=1000,
        status="ready",
    )
    ready2 = Asset(
        submission_id=sub.id,
        name="metadata.csv",
        kind="external_url",
        mount_path="/mnt/data/metadata.csv",
        blob_path=f"submissions/{sub.id}/metadata.csv",
        sha256="b" * 64,
        size_bytes=500,
        status="ready",
    )
    pending1 = Asset(
        submission_id=sub.id,
        name="pending-file.bin",
        kind="upload",
        mount_path="/mnt/data/pending-file.bin",
        blob_path=f"submissions/{sub.id}/pending-file.bin",
        sha256=None,
        status="pending",
    )
    db_session.add_all([ready1, ready2, pending1])
    await db_session.flush()

    blob_factory, blob_clients = _make_blob_factory()
    share_factory, share_clients = _make_share_factory()

    copied = await populate_app_share_from_assets(
        db_session,
        app=app,
        submission=sub,
        storage_account_name=_STORAGE_ACCOUNT,
        share_name=_SHARE_NAME,
        blob_client_factory=blob_factory,
        share_client_factory=share_factory,
    )

    # Only the 2 ready assets are copied
    assert len(copied) == 2
    assert set(copied) == {"genome.fa", "metadata.csv"}

    # 2 blob downloads and 2 share uploads
    assert len(blob_clients) == 2
    assert len(share_clients) == 2


# ---------------------------------------------------------------------------
# test_populate_sets_file_metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_populate_sets_file_metadata(db_session: AsyncSession) -> None:
    """File metadata (sha256, asset_id, source) is set on each uploaded file."""
    sub = await _insert_submission(db_session)
    app = await _insert_app(db_session)

    asset = Asset(
        submission_id=sub.id,
        name="genome.fa",
        kind="upload",
        mount_path="/mnt/ref/genome.fa",
        blob_path=f"submissions/{sub.id}/genome.fa",
        sha256="c" * 64,
        size_bytes=1024,
        status="ready",
    )
    db_session.add(asset)
    await db_session.flush()

    blob_factory, blob_clients = _make_blob_factory(b"file contents here")
    share_factory, share_clients = _make_share_factory()

    await populate_app_share_from_assets(
        db_session,
        app=app,
        submission=sub,
        storage_account_name=_STORAGE_ACCOUNT,
        share_name=_SHARE_NAME,
        blob_client_factory=blob_factory,
        share_client_factory=share_factory,
    )

    assert len(share_clients) == 1
    share_client = share_clients[0]

    # Verify set_file_metadata was called
    share_client.set_file_metadata.assert_called_once()
    meta_call = share_client.set_file_metadata.call_args
    metadata = meta_call.kwargs.get("metadata") or meta_call.args[0]

    assert metadata["sha256"] == "c" * 64
    assert metadata["asset_id"] == str(asset.id)
    assert metadata["source"] == "upload"


# ---------------------------------------------------------------------------
# test_populate_returns_empty_when_no_ready_assets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_populate_returns_empty_when_no_ready_assets(
    db_session: AsyncSession,
) -> None:
    """No ready assets → returns empty list, no blob/share calls."""
    sub = await _insert_submission(db_session)
    app = await _insert_app(db_session)

    # Insert only a pending asset
    db_session.add(
        Asset(
            submission_id=sub.id,
            name="pending.bin",
            kind="upload",
            mount_path="/mnt/data/pending.bin",
            blob_path=f"submissions/{sub.id}/pending.bin",
            sha256=None,
            status="pending",
        )
    )
    await db_session.flush()

    blob_factory, blob_clients = _make_blob_factory()
    share_factory, share_clients = _make_share_factory()

    copied = await populate_app_share_from_assets(
        db_session,
        app=app,
        submission=sub,
        storage_account_name=_STORAGE_ACCOUNT,
        share_name=_SHARE_NAME,
        blob_client_factory=blob_factory,
        share_client_factory=share_factory,
    )

    assert copied == []
    assert blob_clients == []
    assert share_clients == []


# ---------------------------------------------------------------------------
# test_populate_uploads_correct_content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_populate_uploads_correct_content(db_session: AsyncSession) -> None:
    """The bytes downloaded from blob are the same bytes uploaded to the share."""
    sub = await _insert_submission(db_session)
    app = await _insert_app(db_session)

    content = b"specific test content for upload verification"

    db_session.add(
        Asset(
            submission_id=sub.id,
            name="data.bin",
            kind="upload",
            mount_path="/mnt/data/data.bin",
            blob_path=f"submissions/{sub.id}/data.bin",
            sha256="d" * 64,
            size_bytes=len(content),
            status="ready",
        )
    )
    await db_session.flush()

    blob_factory, blob_clients = _make_blob_factory(content)
    share_factory, share_clients = _make_share_factory()

    await populate_app_share_from_assets(
        db_session,
        app=app,
        submission=sub,
        storage_account_name=_STORAGE_ACCOUNT,
        share_name=_SHARE_NAME,
        blob_client_factory=blob_factory,
        share_client_factory=share_factory,
    )

    # upload_file was called with the content wrapped in BytesIO
    import io

    share_client = share_clients[0]
    share_client.upload_file.assert_called_once()
    call_args = share_client.upload_file.call_args
    uploaded_io = call_args.args[0]
    assert isinstance(uploaded_io, io.BytesIO)
    assert uploaded_io.read() == content
