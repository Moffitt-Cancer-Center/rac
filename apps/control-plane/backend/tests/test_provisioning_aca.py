"""Tests for provisioning/aca.py — mock Azure SDK, verify contract.

Verifies:
- AC6.1: min_replicas=0, HTTP scaler present, tags passed.
- AC11.1: all 4 required tags appear.
- Transient errors (429/5xx) raise TransientProvisioningError.
- Permanent errors (4xx) raise ProvisioningError(retryable=False).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from rac_control_plane.provisioning.aca import (
    ProvisioningError,
    TransientProvisioningError,
    create_or_update_app,
)
from rac_control_plane.provisioning.tag_builder import build_tier3_tags
from tests.conftest_settings_helper import make_test_settings


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_settings so wrapper tests don't need real env vars."""
    settings = make_test_settings()
    monkeypatch.setattr(
        "rac_control_plane.provisioning.aca.get_settings",
        lambda: settings,
    )

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SLUG = "test-app"
_IMAGE = "myacr.azurecr.io/test-app:abc123"
_TAGS = {
    "rac_env": "dev",
    "rac_app_slug": _SLUG,
    "rac_pi_principal_id": str(uuid4()),
    "rac_submission_id": str(uuid4()),
    "rac_managed_by": "control-plane",
}


def _make_aca_result(fqdn: str = "test-app.internal.env.azurecontainerapps.io") -> MagicMock:
    result = MagicMock()
    result.latest_revision_name = "test-app--rev1"
    result.configuration.ingress.fqdn = fqdn
    result.configuration.ingress.external = False
    return result


def _mock_aca_client(result: Any) -> MagicMock:
    client = MagicMock()
    poller = MagicMock()
    poller.result.return_value = result
    client.container_apps.begin_create_or_update.return_value = poller
    return client


def _http_error(status: int, message: str = "error") -> Any:
    """Build a fake azure HttpResponseError."""
    from azure.core.exceptions import HttpResponseError  # type: ignore[import-untyped]
    resp = SimpleNamespace(status_code=status)
    err = MagicMock()
    err.message = message
    exc = HttpResponseError(message=message)
    exc.response = resp  # type: ignore[attr-defined]
    exc.error = err      # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_app_happy_path() -> None:
    """Happy path: all required fields passed, result returned correctly."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    outcome = await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-123",
        submission_id="sub-456",
        target_port=8080,
        cpu_cores=0.25,
        memory_gb=0.5,
        image_ref=_IMAGE,
        env_vars=[{"name": "PORT", "value": "8080"}],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv.vault.azure.net/secrets/files-key/v1",
        tags=_TAGS,
        aca_client=client,
    )

    assert outcome["fqdn"] == "test-app.internal.env.azurecontainerapps.io"
    assert outcome["revision_name"] == "test-app--rev1"
    assert outcome["ingress_type"] == "internal"


@pytest.mark.asyncio
async def test_sdk_called_with_correct_model() -> None:
    """Verify begin_create_or_update is called with the right container app name."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        aca_client=client,
    )

    assert client.container_apps.begin_create_or_update.called
    call_kwargs = client.container_apps.begin_create_or_update.call_args
    assert call_kwargs.kwargs["container_app_name"] == _SLUG


@pytest.mark.asyncio
async def test_tags_appear_in_container_app_model() -> None:
    """AC11.1: tags dict is passed to the ContainerApp model."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    container_app = call_kwargs.kwargs["container_app_envelope"]
    assert container_app.tags == _TAGS


@pytest.mark.asyncio
async def test_min_replicas_is_zero() -> None:
    """AC6.1: min_replicas=0 to enable scale-to-zero."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    container_app = call_kwargs.kwargs["container_app_envelope"]
    assert container_app.template.scale.min_replicas == 0


@pytest.mark.asyncio
async def test_http_scaler_present() -> None:
    """AC6.1: HTTP scaler must be present for scale-to-zero to work."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    container_app = call_kwargs.kwargs["container_app_envelope"]
    rules = container_app.template.scale.rules
    assert any(r.name == "http" for r in rules), "HTTP scale rule not found"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_transient_error_raises_transient_provisioning_error(status: int) -> None:
    """Transient HTTP errors (429/5xx) raise TransientProvisioningError."""
    client = MagicMock()
    poller = MagicMock()
    poller.result.side_effect = _http_error(status, "Service Unavailable")
    client.container_apps.begin_create_or_update.return_value = poller

    with pytest.raises(TransientProvisioningError) as exc_info:
        await create_or_update_app(
            slug=_SLUG,
            pi_principal_id="pi-1",
            submission_id="sub-1",
            target_port=8000,
            cpu_cores=0.25,
            memory_gb=0.5,
            image_ref=_IMAGE,
            env_vars=[],
            azure_files_share_name=_SLUG,
            storage_account_name="st",
            storage_account_key_secret_uri="https://kv/s/k",
            tags=_TAGS,
            aca_client=client,
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.code == "aca_transient"


@pytest.mark.asyncio
async def test_conflict_raises_provisioning_error_not_retryable() -> None:
    """409 conflict raises ProvisioningError(retryable=False)."""
    client = MagicMock()
    poller = MagicMock()
    poller.result.side_effect = _http_error(409, "Conflict")
    client.container_apps.begin_create_or_update.return_value = poller

    with pytest.raises(ProvisioningError) as exc_info:
        await create_or_update_app(
            slug=_SLUG,
            pi_principal_id="pi-1",
            submission_id="sub-1",
            target_port=8000,
            cpu_cores=0.25,
            memory_gb=0.5,
            image_ref=_IMAGE,
            env_vars=[],
            azure_files_share_name=_SLUG,
            storage_account_name="st",
            storage_account_key_secret_uri="https://kv/s/k",
            tags=_TAGS,
            aca_client=client,
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "aca_conflict"


@pytest.mark.asyncio
async def test_permanent_4xx_raises_provisioning_error() -> None:
    """Other 4xx → ProvisioningError(code='aca_error', retryable=False)."""
    client = MagicMock()
    poller = MagicMock()
    poller.result.side_effect = _http_error(400, "Bad Request")
    client.container_apps.begin_create_or_update.return_value = poller

    with pytest.raises(ProvisioningError) as exc_info:
        await create_or_update_app(
            slug=_SLUG,
            pi_principal_id="pi-1",
            submission_id="sub-1",
            target_port=8000,
            cpu_cores=0.25,
            memory_gb=0.5,
            image_ref=_IMAGE,
            env_vars=[],
            azure_files_share_name=_SLUG,
            storage_account_name="st",
            storage_account_key_secret_uri="https://kv/s/k",
            tags=_TAGS,
            aca_client=client,
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "aca_error"


@pytest.mark.asyncio
async def test_tag_builder_integration() -> None:
    """Tags from build_tier3_tags appear in the ACA call."""
    pi = uuid4()
    sub = uuid4()
    tags = build_tier3_tags(slug=_SLUG, pi_principal_id=pi, submission_id=sub, env="dev")

    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id=str(pi),
        submission_id=str(sub),
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="st",
        storage_account_key_secret_uri="https://kv/s/k",
        tags=tags,
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    passed_tags = call_kwargs.kwargs["container_app_envelope"].tags
    for key in {"rac_env", "rac_app_slug", "rac_pi_principal_id", "rac_submission_id", "rac_managed_by"}:
        assert key in passed_tags, f"Required tag {key!r} missing from ACA call"
