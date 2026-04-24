"""Tests for ACA volume mount wiring — asset_mounts parameter (Phase 8).

Verifies:
- test_aca_app_has_volume_mounts_per_asset: ContainerApp built with 2 assets
  → volume_mounts list has 2 entries with correct mount_paths + sub_paths.
- test_aca_app_no_asset_mounts_uses_legacy_default: no asset_mounts → single
  /mnt/assets mount (backward compat).
- test_orchestrator_calls_populate_before_aca_create: mocked sequence confirms
  populate_fn is called before aca_fn.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

import pytest

from rac_control_plane.provisioning.aca import (
    ProvisioningError,
    TransientProvisioningError,
    _build_volume_mounts,
    create_or_update_app,
)
from rac_control_plane.provisioning.tag_builder import build_tier3_tags
from tests.conftest_settings_helper import make_test_settings

_SLUG = "test-app"
_IMAGE = "myacr.azurecr.io/test-app:abc123"
_TAGS: dict[str, str] = {
    "rac_env": "dev",
    "rac_app_slug": _SLUG,
    "rac_pi_principal_id": str(uuid4()),
    "rac_submission_id": str(uuid4()),
    "rac_managed_by": "control-plane",
}


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_test_settings()
    monkeypatch.setattr(
        "rac_control_plane.provisioning.aca.get_settings",
        lambda: settings,
    )


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


# ---------------------------------------------------------------------------
# test_aca_app_has_volume_mounts_per_asset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aca_app_has_volume_mounts_per_asset() -> None:
    """ContainerApp with 2 asset_mounts → 2 VolumeMount entries."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    asset_mounts = [
        {
            "name": "genome.fa",
            "mount_path": "/mnt/ref/genome.fa",
            "sub_path": "genome.fa",
        },
        {
            "name": "metadata.csv",
            "mount_path": "/mnt/data/metadata.csv",
            "sub_path": "metadata.csv",
        },
    ]

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8080,
        cpu_cores=0.5,
        memory_gb=1.0,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        asset_mounts=asset_mounts,
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    container_app = call_kwargs.kwargs["container_app_envelope"]
    mounts = container_app.template.containers[0].volume_mounts

    assert len(mounts) == 2

    # Check mount paths
    mount_paths = {m.mount_path for m in mounts}
    assert "/mnt/ref/genome.fa" in mount_paths
    assert "/mnt/data/metadata.csv" in mount_paths

    # Check sub_paths
    sub_paths = {m.sub_path for m in mounts}
    assert "genome.fa" in sub_paths
    assert "metadata.csv" in sub_paths

    # All mounts share the single "assets" volume
    volume_names = {m.volume_name for m in mounts}
    assert volume_names == {"assets"}


@pytest.mark.asyncio
async def test_aca_app_no_asset_mounts_uses_legacy_default() -> None:
    """No asset_mounts → single VolumeMount at /mnt/assets (backward compat)."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8080,
        cpu_cores=0.5,
        memory_gb=1.0,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        asset_mounts=None,
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    container_app = call_kwargs.kwargs["container_app_envelope"]
    mounts = container_app.template.containers[0].volume_mounts

    assert len(mounts) == 1
    assert mounts[0].mount_path == "/mnt/assets"
    assert mounts[0].volume_name == "assets"


@pytest.mark.asyncio
async def test_aca_app_empty_asset_mounts_uses_legacy_default() -> None:
    """Empty asset_mounts list → single VolumeMount at /mnt/assets."""
    result = _make_aca_result()
    client = _mock_aca_client(result)

    await create_or_update_app(
        slug=_SLUG,
        pi_principal_id="pi-1",
        submission_id="sub-1",
        target_port=8080,
        cpu_cores=0.5,
        memory_gb=1.0,
        image_ref=_IMAGE,
        env_vars=[],
        azure_files_share_name=_SLUG,
        storage_account_name="stracdev",
        storage_account_key_secret_uri="https://kv/secrets/key",
        tags=_TAGS,
        asset_mounts=[],
        aca_client=client,
    )

    call_kwargs = client.container_apps.begin_create_or_update.call_args
    container_app = call_kwargs.kwargs["container_app_envelope"]
    mounts = container_app.template.containers[0].volume_mounts

    assert len(mounts) == 1
    assert mounts[0].mount_path == "/mnt/assets"


# ---------------------------------------------------------------------------
# Unit test: _build_volume_mounts helper
# ---------------------------------------------------------------------------

def test_build_volume_mounts_none_returns_legacy() -> None:
    """_build_volume_mounts(None) → single /mnt/assets mount."""
    mounts = _build_volume_mounts(None)
    assert len(mounts) == 1
    assert mounts[0].mount_path == "/mnt/assets"
    assert mounts[0].volume_name == "assets"


def test_build_volume_mounts_empty_returns_legacy() -> None:
    """_build_volume_mounts([]) → single /mnt/assets mount."""
    mounts = _build_volume_mounts([])
    assert len(mounts) == 1
    assert mounts[0].mount_path == "/mnt/assets"


def test_build_volume_mounts_maps_each_asset() -> None:
    """_build_volume_mounts with 3 assets → 3 entries with correct sub_paths."""
    asset_mounts = [
        {"name": "a.csv", "mount_path": "/mnt/a.csv", "sub_path": "a.csv"},
        {"name": "b.bin", "mount_path": "/mnt/b.bin", "sub_path": "b.bin"},
        {"name": "c.json", "mount_path": "/mnt/c.json", "sub_path": "c.json"},
    ]
    mounts = _build_volume_mounts(asset_mounts)
    assert len(mounts) == 3

    paths = {m.mount_path for m in mounts}
    assert paths == {"/mnt/a.csv", "/mnt/b.bin", "/mnt/c.json"}

    sub_paths = {m.sub_path for m in mounts}
    assert sub_paths == {"a.csv", "b.bin", "c.json"}


# ---------------------------------------------------------------------------
# test_orchestrator_calls_populate_before_aca_create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_calls_populate_before_aca_create(
    db_session: Any,
) -> None:
    """populate_fn is called BEFORE aca_fn in the orchestrator sequence."""
    from rac_control_plane.data.models import Submission, SubmissionStatus
    from rac_control_plane.services.provisioning.orchestrator import provision_submission
    from tests.conftest_settings_helper import make_test_settings

    settings = make_test_settings()

    # Insert submission in approved state
    submission = Submission(
        slug="order-test",
        status=SubmissionStatus.approved,
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
    )
    db_session.add(submission)
    await db_session.flush()

    call_order: list[str] = []

    async def populate_fn(session: Any, **kwargs: Any) -> list[str]:
        call_order.append("populate")
        return []

    async def files_fn(**kwargs: Any) -> None:
        call_order.append("files")

    async def keys_fn(**kwargs: Any) -> Any:
        call_order.append("keys")
        result = MagicMock()
        result.kid = "test-kid"
        result.version = "v1"
        return result

    async def aca_fn(**kwargs: Any) -> dict[str, str]:
        call_order.append("aca")
        return {"fqdn": "test.example.com", "revision_name": "rev1", "ingress_type": "internal"}

    async def dns_fn(**kwargs: Any) -> None:
        call_order.append("dns")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "rac_control_plane.services.provisioning.orchestrator.get_settings",
            lambda: settings,
        )

        outcome = await provision_submission(
            db_session,
            submission,
            aca_fn=aca_fn,
            dns_fn=dns_fn,
            keys_fn=keys_fn,
            files_fn=files_fn,
            populate_fn=populate_fn,
        )

    # populate must come before aca
    assert "populate" in call_order
    assert "aca" in call_order
    populate_idx = call_order.index("populate")
    aca_idx = call_order.index("aca")
    assert populate_idx < aca_idx, (
        f"populate ({populate_idx}) must run before aca ({aca_idx}); "
        f"order was: {call_order}"
    )
    assert outcome.success is True
