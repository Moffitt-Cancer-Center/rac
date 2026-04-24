"""Tests for provisioning credentials module.

Verifies that client builders use the correct managed-identity configuration
without making real Azure calls.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from rac_control_plane.settings import Settings, get_settings


def _make_settings(**overrides: object) -> Settings:
    """Build a minimal Settings instance for testing."""
    defaults: dict[str, object] = {
        "env": "dev",
        "institution_name": "Test",
        "parent_domain": "test.local",
        "brand_logo_url": "https://example.com/logo.png",
        "idp_tenant_id": "tenant",
        "idp_client_id": "client",
        "idp_api_client_id": "api-client",
        "pg_host": "localhost",
        "pg_db": "testdb",
        "pg_user": "user",
        "pg_password": "pass",
        "kv_uri": "https://test-kv.vault.azure.net/",
        "blob_account_url": "https://test.blob.core.windows.net/",
        "acr_login_server": "test.azurecr.io",
        "aca_env_resource_id": (
            "/subscriptions/sub/resourceGroups/rg"
            "/providers/Microsoft.App/managedEnvironments/env"
        ),
        "scan_severity_gate": "high",
        "approver_role_research": "research_approver",
        "approver_role_it": "it_approver",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class TestGetAzureCredential:
    """Tests for get_azure_credential()."""

    def test_uses_managed_identity_client_id_from_settings(self) -> None:
        """get_azure_credential() passes managed_identity_client_id from settings."""
        mi_client_id = str(uuid4())
        test_settings = _make_settings(
            controlplane_managed_identity_client_id=mi_client_id
        )

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential"
        ) as mock_cred_cls:
            mock_cred_cls.return_value = MagicMock()

            # Clear lru_cache before each test to avoid cross-test contamination
            from rac_control_plane.provisioning import credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()

            creds_mod.get_azure_credential()

            mock_cred_cls.assert_called_once_with(
                managed_identity_client_id=mi_client_id,
            )

    def test_returns_singleton(self) -> None:
        """get_azure_credential() returns the same instance on repeated calls."""
        test_settings = _make_settings(
            controlplane_managed_identity_client_id="test-mi-client-id"
        )

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential"
        ) as mock_cred_cls:
            fake_credential = MagicMock()
            mock_cred_cls.return_value = fake_credential

            from rac_control_plane.provisioning import credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()

            cred1 = creds_mod.get_azure_credential()
            cred2 = creds_mod.get_azure_credential()

            assert cred1 is cred2
            assert mock_cred_cls.call_count == 1

    def test_empty_managed_identity_passes_none(self) -> None:
        """Empty controlplane_managed_identity_client_id passes None (not empty string)."""
        test_settings = _make_settings(controlplane_managed_identity_client_id="")

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential"
        ) as mock_cred_cls:
            mock_cred_cls.return_value = MagicMock()

            from rac_control_plane.provisioning import credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()

            creds_mod.get_azure_credential()

            mock_cred_cls.assert_called_once_with(managed_identity_client_id=None)


class TestGetGraphClient:
    """Tests for get_graph_client()."""

    def test_uses_graph_microsoft_com_scope(self) -> None:
        """get_graph_client() passes the correct Graph scope."""
        test_settings = _make_settings()
        fake_credential = MagicMock()

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential",
            return_value=fake_credential,
        ), patch(
            "rac_control_plane.provisioning.credentials.GraphServiceClient"
        ) as mock_graph_cls:
            mock_graph_cls.return_value = MagicMock()

            from rac_control_plane.provisioning import credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()

            creds_mod.get_graph_client()

            mock_graph_cls.assert_called_once_with(
                fake_credential,
                scopes=["https://graph.microsoft.com/.default"],
            )

    def test_returns_graph_service_client_instance(self) -> None:
        """get_graph_client() returns the GraphServiceClient created by the mock."""
        test_settings = _make_settings()
        fake_credential = MagicMock()
        fake_graph_client = MagicMock()

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential",
            return_value=fake_credential,
        ), patch(
            "rac_control_plane.provisioning.credentials.GraphServiceClient",
            return_value=fake_graph_client,
        ):
            from rac_control_plane.provisioning import credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()

            result = creds_mod.get_graph_client()

        assert result is fake_graph_client


class TestOtherClientBuilders:
    """Smoke tests for ACA, DNS, Key Vault, and Storage client builders."""

    def _setup_patches(
        self, creds_mod: object
    ) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
        """Return patchers for all four SDK client classes."""
        import rac_control_plane.provisioning.credentials as _creds_mod
        _creds_mod.get_azure_credential.cache_clear()
        return MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()

    def test_get_aca_client_uses_subscription_id(self) -> None:
        """get_aca_client() passes subscription_id from settings."""
        sub_id = str(uuid4())
        test_settings = _make_settings(subscription_id=sub_id)
        fake_cred = MagicMock()

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential",
            return_value=fake_cred,
        ), patch(
            "rac_control_plane.provisioning.credentials.ContainerAppsAPIClient"
        ) as mock_cls:
            import rac_control_plane.provisioning.credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()
            creds_mod.get_aca_client()
            mock_cls.assert_called_once_with(
                credential=fake_cred, subscription_id=sub_id
            )

    def test_get_dns_client_uses_subscription_id(self) -> None:
        """get_dns_client() passes subscription_id from settings."""
        sub_id = str(uuid4())
        test_settings = _make_settings(subscription_id=sub_id)
        fake_cred = MagicMock()

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential",
            return_value=fake_cred,
        ), patch(
            "rac_control_plane.provisioning.credentials.DnsManagementClient"
        ) as mock_cls:
            import rac_control_plane.provisioning.credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()
            creds_mod.get_dns_client()
            mock_cls.assert_called_once_with(
                credential=fake_cred, subscription_id=sub_id
            )

    def test_get_key_client_uses_provided_uri(self) -> None:
        """get_key_client() passes kv_uri to KeyClient."""
        test_settings = _make_settings()
        fake_cred = MagicMock()
        kv_uri = "https://my-kv.vault.azure.net/"

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential",
            return_value=fake_cred,
        ), patch(
            "rac_control_plane.provisioning.credentials.KeyClient"
        ) as mock_cls:
            import rac_control_plane.provisioning.credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()
            creds_mod.get_key_client(kv_uri)
            mock_cls.assert_called_once_with(
                vault_url=kv_uri, credential=fake_cred
            )

    def test_get_storage_client_uses_subscription_id(self) -> None:
        """get_storage_client() passes subscription_id from settings."""
        sub_id = str(uuid4())
        test_settings = _make_settings(subscription_id=sub_id)
        fake_cred = MagicMock()

        with patch(
            "rac_control_plane.provisioning.credentials.get_settings",
            return_value=test_settings,
        ), patch(
            "rac_control_plane.provisioning.credentials.DefaultAzureCredential",
            return_value=fake_cred,
        ), patch(
            "rac_control_plane.provisioning.credentials.StorageManagementClient"
        ) as mock_cls:
            import rac_control_plane.provisioning.credentials as creds_mod
            creds_mod.get_azure_credential.cache_clear()
            creds_mod.get_storage_client()
            mock_cls.assert_called_once_with(
                credential=fake_cred, subscription_id=sub_id
            )
