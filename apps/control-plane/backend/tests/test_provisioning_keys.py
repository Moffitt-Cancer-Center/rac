"""Tests for provisioning/keys.py — mock Azure Key Vault SDK."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError
from rac_control_plane.provisioning.keys import KeyIdentifier, create_signing_key
from rac_control_plane.provisioning.tag_builder import build_tier3_tags
from tests.conftest_settings_helper import make_test_settings


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_test_settings()
    monkeypatch.setattr("rac_control_plane.provisioning.keys.get_settings", lambda: settings)

_TAGS = {
    "rac_env": "dev",
    "rac_app_slug": "myapp",
    "rac_pi_principal_id": str(uuid4()),
    "rac_submission_id": str(uuid4()),
    "rac_managed_by": "control-plane",
}


def _mock_key_result(
    kid: str = "https://kv.vault.azure.net/keys/rac-app-myapp-v1/abc123",
    version: str = "abc123",
) -> MagicMock:
    result = MagicMock()
    result.id = kid
    result.properties.version = version
    return result


def _mock_key_client(kid: str = "https://kv.vault.azure.net/keys/rac-app-myapp-v1/abc123") -> MagicMock:
    client = MagicMock()
    client.create_ec_key.return_value = _mock_key_result(kid)
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
async def test_create_signing_key_returns_identifier() -> None:
    kid = "https://kv.vault.azure.net/keys/rac-app-myapp-v1/abc123"
    client = _mock_key_client(kid)

    result = await create_signing_key(app_slug="myapp", tags=_TAGS, key_client=client)

    assert isinstance(result, KeyIdentifier)
    assert result.kid == kid
    assert result.key_name == "rac-app-myapp-v1"
    assert result.version == "abc123"


@pytest.mark.asyncio
async def test_key_name_uses_slug() -> None:
    client = _mock_key_client()

    await create_signing_key(app_slug="cool-slug", tags=_TAGS, key_client=client)

    call_args = client.create_ec_key.call_args
    assert call_args.kwargs["name"] == "rac-app-cool-slug-v1"


@pytest.mark.asyncio
async def test_key_uses_p256_curve() -> None:
    from azure.keyvault.keys import KeyCurveName  # type: ignore[import-untyped]

    client = _mock_key_client()

    await create_signing_key(app_slug="myapp", tags=_TAGS, key_client=client)

    call_args = client.create_ec_key.call_args
    assert call_args.kwargs["curve"] == KeyCurveName.p_256


@pytest.mark.asyncio
async def test_tags_passed_to_create_ec_key() -> None:
    client = _mock_key_client()

    pi = uuid4()
    sub = uuid4()
    tags = build_tier3_tags(slug="myapp", pi_principal_id=pi, submission_id=sub, env="dev")

    await create_signing_key(app_slug="myapp", tags=tags, key_client=client)

    call_args = client.create_ec_key.call_args
    assert call_args.kwargs["tags"] == tags
    for key in {"rac_env", "rac_app_slug", "rac_pi_principal_id", "rac_submission_id"}:
        assert key in call_args.kwargs["tags"]


@pytest.mark.asyncio
async def test_sign_and_verify_operations() -> None:
    """Key operations must include sign and verify."""
    client = _mock_key_client()

    await create_signing_key(app_slug="myapp", tags=_TAGS, key_client=client)

    call_args = client.create_ec_key.call_args
    ops = call_args.kwargs["key_operations"]
    assert "sign" in ops
    assert "verify" in ops


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_error(status: int) -> None:
    client = MagicMock()
    client.create_ec_key.side_effect = _http_error(status)

    with pytest.raises(TransientProvisioningError) as exc_info:
        await create_signing_key(app_slug="myapp", tags=_TAGS, key_client=client)

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "kv_transient"


@pytest.mark.asyncio
async def test_conflict_raises_permanent_error() -> None:
    client = MagicMock()
    client.create_ec_key.side_effect = _http_error(409, "Conflict")

    with pytest.raises(ProvisioningError) as exc_info:
        await create_signing_key(app_slug="myapp", tags=_TAGS, key_client=client)

    assert exc_info.value.retryable is False
    assert exc_info.value.code == "kv_conflict"


@pytest.mark.asyncio
async def test_permanent_4xx_raises_error() -> None:
    client = MagicMock()
    client.create_ec_key.side_effect = _http_error(403, "Forbidden")

    with pytest.raises(ProvisioningError) as exc_info:
        await create_signing_key(app_slug="myapp", tags=_TAGS, key_client=client)

    assert exc_info.value.retryable is False
    assert exc_info.value.code == "kv_error"
