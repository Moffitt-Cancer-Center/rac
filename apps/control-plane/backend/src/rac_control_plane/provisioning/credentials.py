# pattern: Imperative Shell
"""Azure credential singletons and SDK client builders.

All client builders use the same DefaultAzureCredential configured with the
Control Plane's user-assigned managed identity client ID so that the UAMI is
preferred over any system-assigned identity or ambient developer credential.
"""

import functools

from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.mgmt.appcontainers import ContainerAppsAPIClient
from azure.mgmt.dns import DnsManagementClient
from azure.mgmt.storage import StorageManagementClient
from msgraph import GraphServiceClient

from rac_control_plane.settings import get_settings


@functools.lru_cache(maxsize=1)
def get_azure_credential() -> DefaultAzureCredential:
    """Return a singleton DefaultAzureCredential.

    Configures ``managed_identity_client_id`` from settings so that the
    user-assigned managed identity on the ACA app is preferred over any
    system-assigned identity or workstation credential.
    """
    settings = get_settings()
    return DefaultAzureCredential(
        managed_identity_client_id=settings.controlplane_managed_identity_client_id
        or None,
    )


def get_graph_client() -> GraphServiceClient:
    """Return a GraphServiceClient using the shared credential.

    Scope is always ``https://graph.microsoft.com/.default`` (app-only, via
    the managed identity's granted ``User.Read.All`` application permission).
    """
    credential = get_azure_credential()
    scopes = ["https://graph.microsoft.com/.default"]
    return GraphServiceClient(credential, scopes=scopes)


def get_aca_client() -> ContainerAppsAPIClient:
    """Return a ContainerAppsAPIClient for the configured subscription."""
    settings = get_settings()
    return ContainerAppsAPIClient(
        credential=get_azure_credential(),
        subscription_id=settings.subscription_id,
    )


def get_dns_client() -> DnsManagementClient:
    """Return a DnsManagementClient for the configured subscription."""
    settings = get_settings()
    return DnsManagementClient(
        credential=get_azure_credential(),
        subscription_id=settings.subscription_id,
    )


def get_key_client(kv_uri: str) -> KeyClient:
    """Return a KeyClient for the given Key Vault URI."""
    return KeyClient(vault_url=kv_uri, credential=get_azure_credential())


def get_storage_client() -> StorageManagementClient:
    """Return a StorageManagementClient for the configured subscription."""
    settings = get_settings()
    return StorageManagementClient(
        credential=get_azure_credential(),
        subscription_id=settings.subscription_id,
    )
