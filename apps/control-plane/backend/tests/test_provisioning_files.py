"""Tests for provisioning/files.py — mock Azure Storage SDK."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError
from rac_control_plane.provisioning.files import ensure_app_share
from rac_control_plane.provisioning.tag_builder import build_tier3_tags
from tests.conftest_settings_helper import make_test_settings


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_test_settings()
    monkeypatch.setattr("rac_control_plane.provisioning.files.get_settings", lambda: settings)

_TAGS = {
    "rac_env": "dev",
    "rac_app_slug": "myapp",
    "rac_pi_principal_id": str(uuid4()),
    "rac_submission_id": str(uuid4()),
    "rac_managed_by": "control-plane",
}


def _mock_storage_client(resource_id: str = "/sub/rg/storage/st/fileServices/default/shares/myapp") -> MagicMock:
    result = MagicMock()
    result.id = resource_id
    client = MagicMock()
    client.file_shares.create.return_value = result
    return client


def _http_error(status: int, message: str = "error") -> Any:
    from azure.core.exceptions import HttpResponseError  # type: ignore[import-untyped]
    resp = SimpleNamespace(status_code=status)
    err = MagicMock()
    err.message = message
    exc = HttpResponseError(message=message)
    exc.response = resp  # type: ignore[attr-defined]
    exc.error = err      # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_app_share_returns_resource_id() -> None:
    rid = "/sub/rg/storage/st/shares/myapp"
    client = _mock_storage_client(resource_id=rid)

    result = await ensure_app_share(
        storage_account_name="stracdev",
        share_name="myapp",
        tags=_TAGS,
        storage_client=client,
    )
    assert result == rid


@pytest.mark.asyncio
async def test_sdk_called_with_correct_args() -> None:
    client = _mock_storage_client()

    await ensure_app_share(
        storage_account_name="mystorageaccount",
        share_name="cool-app",
        tags=_TAGS,
        storage_client=client,
    )

    call_kwargs = client.file_shares.create.call_args.kwargs
    assert call_kwargs["account_name"] == "mystorageaccount"
    assert call_kwargs["share_name"] == "cool-app"


@pytest.mark.asyncio
async def test_tags_passed_as_metadata() -> None:
    client = _mock_storage_client()

    pi = uuid4()
    sub = uuid4()
    tags = build_tier3_tags(slug="myapp", pi_principal_id=pi, submission_id=sub, env="dev")

    await ensure_app_share(
        storage_account_name="st",
        share_name="myapp",
        tags=tags,
        storage_client=client,
    )

    call_kwargs = client.file_shares.create.call_args.kwargs
    file_share = call_kwargs["file_share"]
    assert file_share.metadata == tags
    for key in {"rac_env", "rac_app_slug", "rac_pi_principal_id", "rac_submission_id"}:
        assert key in file_share.metadata


@pytest.mark.asyncio
async def test_share_quota_is_100() -> None:
    """Default quota for all shares is 100 GiB."""
    client = _mock_storage_client()

    await ensure_app_share(
        storage_account_name="st",
        share_name="myapp",
        tags=_TAGS,
        storage_client=client,
    )

    call_kwargs = client.file_shares.create.call_args.kwargs
    assert call_kwargs["file_share"].share_quota == 100


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_error(status: int) -> None:
    client = MagicMock()
    client.file_shares.create.side_effect = _http_error(status)

    with pytest.raises(TransientProvisioningError) as exc_info:
        await ensure_app_share(
            storage_account_name="st",
            share_name="myapp",
            tags=_TAGS,
            storage_client=client,
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.code == "files_transient"


@pytest.mark.asyncio
async def test_conflict_raises_permanent_error() -> None:
    client = MagicMock()
    client.file_shares.create.side_effect = _http_error(409, "Conflict")

    with pytest.raises(ProvisioningError) as exc_info:
        await ensure_app_share(
            storage_account_name="st",
            share_name="myapp",
            tags=_TAGS,
            storage_client=client,
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "files_conflict"


@pytest.mark.asyncio
async def test_permanent_4xx_error() -> None:
    client = MagicMock()
    client.file_shares.create.side_effect = _http_error(403, "Forbidden")

    with pytest.raises(ProvisioningError) as exc_info:
        await ensure_app_share(
            storage_account_name="st",
            share_name="myapp",
            tags=_TAGS,
            storage_client=client,
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "files_error"
