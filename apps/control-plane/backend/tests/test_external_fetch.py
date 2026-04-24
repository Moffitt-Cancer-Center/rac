"""Tests for services/assets/external_fetch.py.

Verifies:
- AC8.2: Reachable URL + matching sha256 → asset row 'ready'.
- AC8.3: Reachable URL + mismatching sha256 → asset row 'hash_mismatch',
         HashMismatchError raised, blob deleted.
- AC8.4: Unreachable URL → asset row 'unreachable', ExternalAssetError raised.
- Non-HTTPS URL rejected immediately.
- Large file (10 MB) streaming path produces correct sha256.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import Asset, Submission
from rac_control_plane.services.assets.external_fetch import (
    ExternalAssetError,
    HashMismatchError,
    fetch_external_asset,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCOUNT_URL = "https://teststorage.blob.core.windows.net/"
_CONTAINER = "researcher-uploads"
_EXTERNAL_URL = "https://example.com/data/genome.fa"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _insert_submission(session: AsyncSession) -> Submission:
    """Insert a minimal submission row and return it."""
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


def _make_blob_factory() -> tuple[object, object]:
    """Return (factory, store) where store collects uploaded data.

    The blob client stores uploaded bytes in store['data'] and tracks
    delete_blob calls in store['deleted'].
    """
    from unittest.mock import MagicMock

    store: dict[str, object] = {"data": None, "deleted": False}
    mock_client = MagicMock()

    def upload_blob(data: bytes, overwrite: bool = False) -> None:
        store["data"] = data

    def delete_blob() -> None:
        store["deleted"] = True

    mock_client.upload_blob.side_effect = upload_blob
    mock_client.delete_blob.side_effect = delete_blob

    def factory(account_url: str, container: str, path: str) -> object:
        return mock_client

    return factory, store


# ---------------------------------------------------------------------------
# AC8.2: Reachable URL + matching sha256 → ready
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reachable_matching_sha_succeeds(db_session: AsyncSession) -> None:
    """AC8.2: Matching sha256 → asset status 'ready'."""
    sub = await _insert_submission(db_session)
    data = b"reference genome data chunk"
    expected_sha = _sha256(data)
    factory, store = _make_blob_factory()

    with respx.mock:
        respx.get(_EXTERNAL_URL).mock(
            return_value=httpx.Response(200, content=data)
        )

        asset = await fetch_external_asset(
            db_session,
            submission_id=sub.id,
            asset_name="genome.fa",
            url=_EXTERNAL_URL,  # type: ignore[arg-type]
            declared_sha256=expected_sha,
            mount_path="/mnt/ref/genome.fa",
            blob_client_factory=factory,
            account_url=_ACCOUNT_URL,
            container_name=_CONTAINER,
            enforce_https=True,
        )

    assert asset.status == "ready"
    assert asset.sha256 == expected_sha
    assert asset.submission_id == sub.id
    assert asset.name == "genome.fa"
    assert asset.kind == "external_url"
    assert asset.mount_path == "/mnt/ref/genome.fa"
    assert asset.blob_uri is not None
    # The blob was uploaded
    assert store["data"] == data
    assert store["deleted"] is False


# ---------------------------------------------------------------------------
# AC8.3: Reachable URL + mismatching sha256 → hash_mismatch + error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reachable_mismatching_sha_raises(db_session: AsyncSession) -> None:
    """AC8.3: Mismatching sha256 → HashMismatchError, asset status 'hash_mismatch', blob deleted."""
    sub = await _insert_submission(db_session)
    data = b"actual content of the file"
    actual_sha = _sha256(data)
    declared_sha = "b" * 64  # wrong hash

    factory, store = _make_blob_factory()

    with respx.mock:
        respx.get(_EXTERNAL_URL).mock(
            return_value=httpx.Response(200, content=data)
        )

        with pytest.raises(HashMismatchError) as exc_info:
            await fetch_external_asset(
                db_session,
                submission_id=sub.id,
                asset_name="genome.fa",
                url=_EXTERNAL_URL,  # type: ignore[arg-type]
                declared_sha256=declared_sha,
                mount_path="/mnt/ref/genome.fa",
                blob_client_factory=factory,
                account_url=_ACCOUNT_URL,
                container_name=_CONTAINER,
                enforce_https=True,
            )

    exc = exc_info.value
    assert exc.code == "hash_mismatch"
    assert exc.expected == declared_sha
    assert exc.actual == actual_sha

    # Blob was deleted after mismatch
    assert store["deleted"] is True

    # Asset row was inserted with hash_mismatch status
    result = await db_session.execute(
        select(Asset).where(Asset.submission_id == sub.id)
    )
    asset = result.scalar_one()
    assert asset.status == "hash_mismatch"
    assert asset.expected_sha256 == declared_sha
    assert asset.actual_sha256 == actual_sha
    assert asset.blob_uri is None


# ---------------------------------------------------------------------------
# AC8.4: Unreachable URL → unreachable status + error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unreachable_url_raises(db_session: AsyncSession) -> None:
    """AC8.4: ConnectError → ExternalAssetError(code='unreachable'), asset status 'unreachable'."""
    sub = await _insert_submission(db_session)
    factory, _ = _make_blob_factory()

    with respx.mock:
        respx.get(_EXTERNAL_URL).mock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(ExternalAssetError) as exc_info:
            await fetch_external_asset(
                db_session,
                submission_id=sub.id,
                asset_name="genome.fa",
                url=_EXTERNAL_URL,  # type: ignore[arg-type]
                declared_sha256="a" * 64,
                mount_path="/mnt/ref/genome.fa",
                blob_client_factory=factory,
                account_url=_ACCOUNT_URL,
                container_name=_CONTAINER,
                enforce_https=True,
            )

    assert exc_info.value.code == "unreachable"

    # Asset row inserted with unreachable status
    result = await db_session.execute(
        select(Asset).where(Asset.submission_id == sub.id)
    )
    asset = result.scalar_one()
    assert asset.status == "unreachable"
    assert asset.blob_uri is None


# ---------------------------------------------------------------------------
# Non-HTTPS URL rejected immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_https_rejected(db_session: AsyncSession) -> None:
    """HTTP (non-HTTPS) URL is rejected before any network call."""
    sub = await _insert_submission(db_session)
    factory, _ = _make_blob_factory()

    with pytest.raises(ExternalAssetError) as exc_info:
        await fetch_external_asset(
            db_session,
            submission_id=sub.id,
            asset_name="data.csv",
            url="http://example.com/data.csv",  # type: ignore[arg-type]
            declared_sha256="a" * 64,
            mount_path="/mnt/data/data.csv",
            blob_client_factory=factory,
            account_url=_ACCOUNT_URL,
            container_name=_CONTAINER,
            enforce_https=True,
        )

    assert exc_info.value.code == "non_https"

    # No asset row should be inserted
    result = await db_session.execute(
        select(Asset).where(Asset.submission_id == sub.id)
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Non-HTTPS allowed when enforce_https=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_https_allowed_when_not_enforced(db_session: AsyncSession) -> None:
    """HTTP URL proceeds normally when enforce_https=False."""
    sub = await _insert_submission(db_session)
    data = b"test data"
    expected_sha = _sha256(data)
    factory, store = _make_blob_factory()
    http_url = "http://internal.example.com/data.csv"

    with respx.mock:
        respx.get(http_url).mock(return_value=httpx.Response(200, content=data))

        asset = await fetch_external_asset(
            db_session,
            submission_id=sub.id,
            asset_name="data.csv",
            url=http_url,  # type: ignore[arg-type]
            declared_sha256=expected_sha,
            mount_path="/mnt/data/data.csv",
            blob_client_factory=factory,
            account_url=_ACCOUNT_URL,
            container_name=_CONTAINER,
            enforce_https=False,
        )

    assert asset.status == "ready"


# ---------------------------------------------------------------------------
# Large file streaming (10 MB payload)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_large_file_sha256_correct(db_session: AsyncSession) -> None:
    """Streaming a 10 MB payload produces the correct sha256."""
    sub = await _insert_submission(db_session)
    # 10 MB of deterministic pseudorandom bytes
    large_data = bytes(range(256)) * (10 * 1024 * 1024 // 256)
    expected_sha = _sha256(large_data)
    factory, store = _make_blob_factory()

    with respx.mock:
        respx.get(_EXTERNAL_URL).mock(
            return_value=httpx.Response(200, content=large_data)
        )

        asset = await fetch_external_asset(
            db_session,
            submission_id=sub.id,
            asset_name="big-data.bin",
            url=_EXTERNAL_URL,  # type: ignore[arg-type]
            declared_sha256=expected_sha,
            mount_path="/mnt/data/big-data.bin",
            blob_client_factory=factory,
            account_url=_ACCOUNT_URL,
            container_name=_CONTAINER,
            enforce_https=True,
        )

    assert asset.status == "ready"
    assert asset.sha256 == expected_sha
    assert asset.size_bytes == len(large_data)
    # Uploaded data matches what was fetched
    assert store["data"] == large_data


# ---------------------------------------------------------------------------
# Timeout → unreachable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_produces_unreachable(db_session: AsyncSession) -> None:
    """A timeout exception results in status='unreachable' asset row."""
    sub = await _insert_submission(db_session)
    factory, _ = _make_blob_factory()

    with respx.mock:
        respx.get(_EXTERNAL_URL).mock(
            side_effect=httpx.TimeoutException("read timeout")
        )

        with pytest.raises(ExternalAssetError) as exc_info:
            await fetch_external_asset(
                db_session,
                submission_id=sub.id,
                asset_name="genome.fa",
                url=_EXTERNAL_URL,  # type: ignore[arg-type]
                declared_sha256="a" * 64,
                mount_path="/mnt/ref/genome.fa",
                blob_client_factory=factory,
                account_url=_ACCOUNT_URL,
                container_name=_CONTAINER,
                enforce_https=True,
            )

    assert exc_info.value.code == "unreachable"

    result = await db_session.execute(
        select(Asset).where(Asset.submission_id == sub.id)
    )
    asset = result.scalar_one()
    assert asset.status == "unreachable"
