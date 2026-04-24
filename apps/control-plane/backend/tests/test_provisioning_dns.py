"""Tests for provisioning/dns.py — mock Azure DNS SDK."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from rac_control_plane.provisioning.aca import ProvisioningError, TransientProvisioningError
from rac_control_plane.provisioning.dns import upsert_a_record
from rac_control_plane.provisioning.tag_builder import build_tier3_tags
from tests.conftest_settings_helper import make_test_settings


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_test_settings()
    monkeypatch.setattr("rac_control_plane.provisioning.dns.get_settings", lambda: settings)

_TAGS = {
    "rac_env": "dev",
    "rac_app_slug": "test-app",
    "rac_pi_principal_id": str(uuid4()),
    "rac_submission_id": str(uuid4()),
    "rac_managed_by": "control-plane",
}


def _mock_dns_client(resource_id: str = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/dnszones/z/A/app") -> MagicMock:
    result = MagicMock()
    result.id = resource_id
    client = MagicMock()
    client.record_sets.create_or_update.return_value = result
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
async def test_upsert_a_record_returns_resource_id() -> None:
    rid = "/sub/rg/dns/A/app"
    client = _mock_dns_client(resource_id=rid)

    result = await upsert_a_record(
        zone_name="rac.example.com",
        subdomain="myapp",
        ip_address="10.0.0.1",
        tags=_TAGS,
        dns_client=client,
    )
    assert result == rid


@pytest.mark.asyncio
async def test_sdk_called_with_correct_args() -> None:
    client = _mock_dns_client()

    await upsert_a_record(
        zone_name="example.com",
        subdomain="testapp",
        ip_address="192.168.1.1",
        tags=_TAGS,
        dns_client=client,
    )

    call_kwargs = client.record_sets.create_or_update.call_args
    assert call_kwargs.kwargs["zone_name"] == "example.com"
    assert call_kwargs.kwargs["relative_record_set_name"] == "testapp"
    assert call_kwargs.kwargs["record_type"] == "A"


@pytest.mark.asyncio
async def test_tags_passed_as_metadata() -> None:
    """DNS uses metadata= (not tags=) in the SDK."""
    client = _mock_dns_client()

    await upsert_a_record(
        zone_name="example.com",
        subdomain="app",
        ip_address="1.2.3.4",
        tags=_TAGS,
        dns_client=client,
    )

    call_kwargs = client.record_sets.create_or_update.call_args
    record_set = call_kwargs.kwargs["parameters"]
    assert record_set.metadata == _TAGS


@pytest.mark.asyncio
async def test_tag_builder_integration() -> None:
    """Tags from build_tier3_tags appear in the DNS call."""
    pi = uuid4()
    sub = uuid4()
    tags = build_tier3_tags(slug="myapp", pi_principal_id=pi, submission_id=sub, env="dev")
    client = _mock_dns_client()

    await upsert_a_record(
        zone_name="example.com",
        subdomain="myapp",
        ip_address="10.0.0.1",
        tags=tags,
        dns_client=client,
    )

    call_kwargs = client.record_sets.create_or_update.call_args
    metadata = call_kwargs.kwargs["parameters"].metadata
    for key in {"rac_env", "rac_app_slug", "rac_pi_principal_id", "rac_submission_id"}:
        assert key in metadata


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_error(status: int) -> None:
    client = MagicMock()
    client.record_sets.create_or_update.side_effect = _http_error(status)

    with pytest.raises(TransientProvisioningError) as exc_info:
        await upsert_a_record(
            zone_name="z.com",
            subdomain="app",
            ip_address="1.2.3.4",
            tags=_TAGS,
            dns_client=client,
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.code == "dns_transient"


@pytest.mark.asyncio
async def test_dns_conflict_raises_permanent_error() -> None:
    client = MagicMock()
    client.record_sets.create_or_update.side_effect = _http_error(409, "Conflict")

    with pytest.raises(ProvisioningError) as exc_info:
        await upsert_a_record(
            zone_name="z.com",
            subdomain="app",
            ip_address="1.2.3.4",
            tags=_TAGS,
            dns_client=client,
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "dns_conflict"


@pytest.mark.asyncio
async def test_permanent_4xx_error() -> None:
    client = MagicMock()
    client.record_sets.create_or_update.side_effect = _http_error(403, "Forbidden")

    with pytest.raises(ProvisioningError) as exc_info:
        await upsert_a_record(
            zone_name="z.com",
            subdomain="app",
            ip_address="1.2.3.4",
            tags=_TAGS,
            dns_client=client,
        )
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "dns_error"
