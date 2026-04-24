# pattern: Imperative Shell
"""Azure Container Apps (ACA) provisioning wrapper.

Creates or updates a researcher app in ACA with:
- HTTP-based scale rule (min_replicas=0 requires event-based scaler).
- Azure Files volume mount at /mnt/assets.
- User-assigned managed identity.
- ACR pull via managed identity.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Typed error classes
# ---------------------------------------------------------------------------


class ProvisioningError(Exception):
    """Permanent or non-retryable provisioning failure."""

    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        self.code = code
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"{code}: {detail}")


class TransientProvisioningError(ProvisioningError):
    """Transient provisioning failure that warrants a retry."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, retryable=True)


# ---------------------------------------------------------------------------
# Main provisioning function
# ---------------------------------------------------------------------------


async def create_or_update_app(
    slug: str,
    pi_principal_id: str,
    submission_id: str,
    target_port: int,
    cpu_cores: float,
    memory_gb: float,
    image_ref: str,
    env_vars: list[dict[str, str]],
    azure_files_share_name: str,
    storage_account_name: str,
    storage_account_key_secret_uri: str,
    tags: dict[str, str],
    *,
    asset_mounts: list[dict[str, str]] | None = None,
    aca_client: Any = None,
) -> dict[str, Any]:
    """Create or update an ACA container app for the given submission.

    Returns a dict with keys: fqdn, revision_name, ingress_type.

    Args:
        slug: App slug (used as container app name).
        pi_principal_id: PI's principal ID (for environment variable injection).
        submission_id: Submission UUID (for environment variable injection).
        target_port: Port the container listens on.
        cpu_cores: CPU allocation (e.g. 0.25).
        memory_gb: Memory allocation in GiB (e.g. 0.5).
        image_ref: Full image reference including tag.
        env_vars: List of {'name': str, 'value': str} env var dicts.
        azure_files_share_name: Name of the Azure Files share to mount.
        storage_account_name: Storage account that hosts the file share.
        storage_account_key_secret_uri: KV secret URI for the storage account key.
        tags: Azure resource tags (must include AC11.1 tags).
        asset_mounts: Optional list of per-asset mount specs, each a dict with
            keys 'name' (asset.name), 'mount_path' (absolute container path),
            and 'sub_path' (path within the Files share). When provided, one
            VolumeMount per asset is added alongside the base "assets" volume.
        aca_client: Optional injected ACA client for testing.

    Returns:
        Dict with fqdn, revision_name, ingress_type.

    Raises:
        TransientProvisioningError: On 429/5xx — caller should retry.
        ProvisioningError: On permanent errors (conflict, bad request, etc.).
    """
    from azure.core.exceptions import HttpResponseError
    from azure.mgmt.appcontainers import ContainerAppsAPIClient
    from azure.mgmt.appcontainers.models import (
        Configuration,
        Container,
        ContainerApp,
        ContainerResources,
        EnvironmentVar,
        HttpScaleRule,
        Ingress,
        ManagedServiceIdentity,
        RegistryCredentials,
        Scale,
        ScaleRule,
        Secret,
        Template,
        Volume,
    )

    settings = get_settings()

    if aca_client is None:
        from rac_control_plane.provisioning.credentials import get_azure_credential
        credential = get_azure_credential()
        aca_client = ContainerAppsAPIClient(
            credential=credential,
            subscription_id=settings.subscription_id,
        )

    # Build the secret for the Azure Files storage account key
    files_secret_name = f"files-key-{slug}"
    secrets = [
        Secret(
            name=files_secret_name,
            key_vault_url=storage_account_key_secret_uri,
        )
    ]

    # Build user-assigned identity dict
    user_assigned: dict[str, Any] = {settings.managed_identity_resource_id: {}}

    # Build the ContainerApp model
    container_app = ContainerApp(
        location=settings.azure_location,
        tags=tags,
        identity=ManagedServiceIdentity(
            type="UserAssigned",
            user_assigned_identities=user_assigned,
        ),
        configuration=Configuration(
            ingress=Ingress(
                external=False,
                target_port=target_port,
                transport="http",
                allow_insecure=False,
            ),
            registries=[
                RegistryCredentials(
                    server=settings.acr_login_server,
                    identity=settings.managed_identity_resource_id,
                )
            ],
            secrets=secrets,
        ),
        environment_id=settings.aca_env_resource_id,
        workload_profile_name="Consumption",
        template=Template(
            containers=[
                Container(
                    name=slug,
                    image=image_ref,
                    env=[EnvironmentVar(name=e["name"], value=e["value"]) for e in env_vars],
                    volume_mounts=_build_volume_mounts(asset_mounts),
                    resources=ContainerResources(
                        cpu=cpu_cores,
                        memory=f"{memory_gb}Gi",
                    ),
                )
            ],
            scale=Scale(
                min_replicas=0,
                max_replicas=10,
                rules=[
                    ScaleRule(
                        name="http",
                        http=HttpScaleRule(
                            metadata={"concurrentRequests": "100"},
                        ),
                    )
                ],
            ),
            volumes=[
                Volume(
                    name="assets",
                    storage_type="AzureFile",
                    storage_name=azure_files_share_name,
                    mount_options="dir_mode=0755,file_mode=0755,uid=1000,gid=1000",
                )
            ],
        ),
    )

    try:
        result = await asyncio.to_thread(
            lambda: aca_client.container_apps.begin_create_or_update(
                resource_group_name=settings.resource_group,
                container_app_name=slug,
                container_app_envelope=container_app,
            ).result()
        )
        fqdn = ""
        revision_name = ""
        ingress_type = "internal"
        if result.configuration and result.configuration.ingress:
            fqdn = result.configuration.ingress.fqdn or ""
            ingress_type = "external" if result.configuration.ingress.external else "internal"
        if result.latest_revision_name:
            revision_name = result.latest_revision_name
        logger.info("aca_app_provisioned", slug=slug, fqdn=fqdn)
        return {
            "fqdn": fqdn,
            "revision_name": revision_name,
            "ingress_type": ingress_type,
        }

    except HttpResponseError as exc:
        status: int = (exc.response.status_code if exc.response else None) or 0
        msg = str(exc.error.message if exc.error else exc)[:200]

        if status in (429, 500, 502, 503, 504):
            raise TransientProvisioningError(
                code="aca_transient",
                detail=f"ACA HTTP {status}: {msg}",
            ) from exc

        if status == 409:
            raise ProvisioningError(
                code="aca_conflict",
                detail=f"ACA conflict creating {slug}: {msg}",
                retryable=False,
            ) from exc

        if 400 <= status < 500:
            raise ProvisioningError(
                code="aca_error",
                detail=f"ACA error {status} creating {slug}: {msg}",
                retryable=False,
            ) from exc

        # Unknown status — treat as transient
        raise TransientProvisioningError(
            code="aca_transient",
            detail=f"ACA unexpected error {status}: {msg}",
        ) from exc


# ---------------------------------------------------------------------------
# Volume mount helpers
# ---------------------------------------------------------------------------


def _build_volume_mounts(
    asset_mounts: list[dict[str, str]] | None,
) -> list[Any]:
    """Build the list of VolumeMount objects for a ContainerApp.

    If asset_mounts is provided (one entry per ready asset), each asset gets
    its own VolumeMount pointing at its declared mount_path with a sub_path
    referencing the asset's filename within the shared Azure Files share.

    If no asset mounts are provided, fall back to the legacy single-volume
    mount at /mnt/assets (preserves backward compat for tests that don't
    pass asset_mounts).

    Args:
        asset_mounts: List of dicts with keys:
            - name:       asset name (used as sub_path in the share)
            - mount_path: absolute path inside the container
            - sub_path:   path of the file within the Azure Files share

    Returns:
        List of VolumeMount objects.
    """
    from azure.mgmt.appcontainers.models import VolumeMount

    if not asset_mounts:
        # Legacy: single mount for the whole share at /mnt/assets
        return [
            VolumeMount(
                volume_name="assets",
                mount_path="/mnt/assets",
            )
        ]

    return [
        VolumeMount(
            volume_name="assets",
            mount_path=mount["mount_path"],
            sub_path=mount.get("sub_path", mount.get("name", "")),
        )
        for mount in asset_mounts
    ]
