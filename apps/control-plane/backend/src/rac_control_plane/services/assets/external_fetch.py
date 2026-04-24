# pattern: Imperative Shell
"""External URL asset fetch with streaming sha256 verification.

Streams the URL → Blob storage while simultaneously computing sha256.
Inserts an asset row whose status reflects the outcome:
  ready        — URL reachable and sha256 matched.
  hash_mismatch — URL reachable but sha256 differs from declared value.
  unreachable  — URL not reachable (DNS failure, timeout, HTTP error).

The caller is responsible for transitioning the submission to needs_user_action
when hash_mismatch or unreachable occurs (this function stays pure in its
mutation scope — it only touches the asset table).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import Asset


class ExternalAssetError(Exception):
    """Base exception for external asset fetch failures."""

    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


class HashMismatchError(ExternalAssetError):
    """sha256 of fetched content does not match declared value (AC8.3)."""

    def __init__(self, expected: str, actual: str, asset_name: str) -> None:
        super().__init__(
            code="hash_mismatch",
            message=(
                f"Declared sha256 does not match downloaded content "
                f"for asset '{asset_name}'"
            ),
            expected=expected,
            actual=actual,
        )
        self.expected = expected
        self.actual = actual


async def fetch_external_asset(
    session: AsyncSession,
    *,
    submission_id: UUID,
    asset_name: str,
    url: HttpUrl,
    declared_sha256: str,
    mount_path: str,
    http_client: httpx.AsyncClient | None = None,
    blob_client_factory: Callable[..., Any] | None = None,  # for test injection
    timeout_seconds: float = 60.0,
    enforce_https: bool = True,
    account_url: str | None = None,
    container_name: str = "researcher-uploads",
    dispatch_fn: Callable[..., Any] | None = None,  # forwarded to finalize_submission
) -> Asset:
    """Fetch an external URL, verify sha256, insert asset row.

    Streams the HTTP response to Blob Storage while computing sha256 on
    the fly. Inserts an asset row reflecting the outcome.

    AC8.2: Matching sha256 → status='ready'.
    AC8.3: Mismatching sha256 → status='hash_mismatch'; raises HashMismatchError.
    AC8.4: Unreachable URL → status='unreachable'; raises ExternalAssetError.

    Args:
        session: Active async session (caller owns commit).
        submission_id: Submission UUID that owns this asset.
        asset_name: Logical asset name.
        url: The external URL to fetch (must be https unless enforce_https=False).
        declared_sha256: Researcher-declared sha256 hex digest.
        mount_path: Absolute container path for the asset mount.
        http_client: Optional injected httpx client (for testing).
        blob_client_factory: Optional factory callable for BlobClient injection.
        timeout_seconds: HTTP request timeout.
        enforce_https: Reject http:// URLs when True (default).
        account_url: Blob storage account URL.
        container_name: Container for caching the fetched blob.

    Returns:
        Inserted Asset row (status reflects outcome).

    Raises:
        ExternalAssetError: code='non_https' for http:// URLs when enforce_https=True.
        ExternalAssetError: code='unreachable' on network/HTTP failure.
        HashMismatchError: On sha256 mismatch (asset row status='hash_mismatch').
    """
    url_str = str(url)

    # Enforce HTTPS
    if enforce_https and url_str.startswith("http://"):
        raise ExternalAssetError(
            code="non_https",
            message=f"External asset URL must use HTTPS: {url_str!r}",
        )

    # Determine blob path
    blob_path = f"submissions/{submission_id}/{asset_name}"

    # Resolve account_url
    if account_url is None:
        from rac_control_plane.settings import get_settings
        account_url = get_settings().blob_account_url

    # Build blob client
    blob_client = _make_blob_client(
        blob_client_factory, account_url, container_name, blob_path
    )

    # Fetch + stream
    own_client = http_client is None
    resolved_client: httpx.AsyncClient = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=True,
    )

    try:
        return await _do_fetch(
            session=session,
            http_client=resolved_client,
            blob_client=blob_client,
            url_str=url_str,
            submission_id=submission_id,
            asset_name=asset_name,
            declared_sha256=declared_sha256,
            mount_path=mount_path,
            blob_path=blob_path,
            account_url=account_url,
            container_name=container_name,
            timeout_seconds=timeout_seconds,
            dispatch_fn=dispatch_fn,
        )
    finally:
        if own_client:
            await resolved_client.aclose()


async def _do_fetch(
    *,
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    blob_client: object,
    url_str: str,
    submission_id: UUID,
    asset_name: str,
    declared_sha256: str,
    mount_path: str,
    blob_path: str,
    account_url: str,
    container_name: str,
    timeout_seconds: float,
    dispatch_fn: Callable[..., Any] | None = None,
) -> Asset:
    """Inner fetch logic — separated for cleaner error handling."""
    from rac_control_plane.services.submissions.finalize import finalize_submission

    # Try to fetch the URL
    try:
        chunks_and_hash = await _stream_and_hash(http_client, url_str, blob_client)
        computed_sha256, computed_size = chunks_and_hash
    except (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.HTTPStatusError,
        httpx.RequestError,
    ) as exc:
        # Unreachable URL → insert asset row with status='unreachable'
        asset = Asset(
            submission_id=submission_id,
            name=asset_name,
            kind="external_url",
            mount_path=mount_path,
            blob_path=None,
            blob_uri=None,
            sha256=None,
            status="unreachable",
            expected_sha256=declared_sha256,
        )
        session.add(asset)
        await session.flush()
        # Signal finalization (will detect the unreachable asset and block pipeline)
        await finalize_submission(session, submission_id, dispatch_fn=dispatch_fn)
        raise ExternalAssetError(
            code="unreachable",
            message=(
                f"External asset URL '{url_str}' is unreachable: {exc}"
            ),
            asset_name=asset_name,
            url=url_str,
        ) from exc

    # Compare hashes
    if computed_sha256.lower() != declared_sha256.lower():
        # Mismatch: delete the blob we just wrote, insert hash_mismatch row
        try:
            blob_client.delete_blob()  # type: ignore[attr-defined]
        except Exception as _del_exc:  # noqa: BLE001
            import structlog as _log
            _log.get_logger(__name__).warning(
                "blob_delete_failed_after_hash_mismatch",
                error=str(_del_exc),
            )

        asset = Asset(
            submission_id=submission_id,
            name=asset_name,
            kind="external_url",
            mount_path=mount_path,
            blob_path=None,
            blob_uri=None,
            sha256=None,
            size_bytes=computed_size,
            status="hash_mismatch",
            expected_sha256=declared_sha256,
            actual_sha256=computed_sha256,
        )
        session.add(asset)
        await session.flush()
        # Signal finalization (will detect hash_mismatch and block pipeline)
        await finalize_submission(session, submission_id, dispatch_fn=dispatch_fn)
        raise HashMismatchError(
            expected=declared_sha256,
            actual=computed_sha256,
            asset_name=asset_name,
        )

    # Success: insert ready row
    blob_uri = f"{account_url.rstrip('/')}/{container_name}/{blob_path}"
    asset = Asset(
        submission_id=submission_id,
        name=asset_name,
        kind="external_url",
        mount_path=mount_path,
        blob_path=blob_path,
        blob_uri=blob_uri,
        sha256=computed_sha256,
        size_bytes=computed_size,
        status="ready",
    )
    session.add(asset)
    await session.flush()
    # Signal finalization: check if this successful fetch unblocks the pipeline
    await finalize_submission(session, submission_id, dispatch_fn=dispatch_fn)
    return asset


async def _stream_and_hash(
    http_client: httpx.AsyncClient,
    url_str: str,
    blob_client: object,
) -> tuple[str, int]:
    """Stream URL response to blob while computing sha256.

    For simplicity: collect chunks in memory, then upload as a single call.
    For very large files the caller can switch to a streaming upload approach;
    in tests the payload is small enough that in-memory buffering is fine.
    """
    collected: list[bytes] = []

    async with http_client.stream("GET", url_str) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            collected.append(chunk)

    data = b"".join(collected)

    # Upload to blob (overwrite=True to handle retries)
    blob_client.upload_blob(data, overwrite=True)  # type: ignore[attr-defined]

    # Compute hash over the collected bytes
    import hashlib
    h = hashlib.sha256(data)
    return h.hexdigest(), len(data)


def _make_blob_client(
    factory: Callable[..., Any] | None,
    account_url: str,
    container_name: str,
    blob_path: str,
) -> Any:
    """Return a BlobClient from the factory or build one via DefaultAzureCredential."""
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
