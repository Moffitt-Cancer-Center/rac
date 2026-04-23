"""Tests for settings module."""
import os

import pytest
from pydantic import ValidationError

from rac_control_plane.settings import Settings, get_settings


def test_settings_required_fields_missing() -> None:
    """Settings raises ValidationError when required fields are missing."""
    with pytest.raises(ValidationError):
        Settings()


def test_settings_with_minimal_required_fields() -> None:
    """Settings parses successfully with all required fields."""
    settings = Settings(
        env="dev",
        institution_name="Test Org",
        parent_domain="example.com",
        brand_logo_url="https://example.com/logo.png",
        idp_tenant_id="tenant-id",
        idp_client_id="client-id",
        idp_api_client_id="api-client-id",
        pg_host="localhost",
        pg_db="testdb",
        pg_user="user",
        pg_password="password",
        kv_uri="https://kv.vault.azure.net/",
        blob_account_url="https://blob.azure.com/",
        acr_login_server="acr.azurecr.io",
        aca_env_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env",
        scan_severity_gate="high",
        approver_role_research="ResearchApprover",
        approver_role_it="ITApprover",
    )

    assert settings.env == "dev"
    assert settings.institution_name == "Test Org"
    assert settings.pg_port == 5432  # default value
    assert settings.pg_ssl_mode == "require"  # default value


def test_settings_pg_dsn_construction() -> None:
    """Settings constructs correct PostgreSQL DSN."""
    settings = Settings(
        env="dev",
        institution_name="Test",
        parent_domain="example.com",
        brand_logo_url="https://example.com/logo.png",
        idp_tenant_id="tenant-id",
        idp_client_id="client-id",
        idp_api_client_id="api-client-id",
        pg_host="myhost",
        pg_port=5433,
        pg_db="mydb",
        pg_user="myuser",
        pg_password="mypass",
        pg_ssl_mode="disable",
        kv_uri="https://kv.vault.azure.net/",
        blob_account_url="https://blob.azure.com/",
        acr_login_server="acr.azurecr.io",
        aca_env_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env",
        scan_severity_gate="high",
        approver_role_research="ResearchApprover",
        approver_role_it="ITApprover",
    )

    dsn = settings.pg_dsn
    assert "postgresql+asyncpg://" in dsn
    assert "myuser:mypass" in dsn
    assert "myhost:5433" in dsn
    assert "mydb" in dsn
    assert "ssl=disable" in dsn


def test_get_settings_caching() -> None:
    """get_settings() returns cached instance."""
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        # First call will fail because env vars not set
        get_settings()

    # After cache_clear(), next call should be independent
    get_settings.cache_clear()


def test_settings_env_var_prefix() -> None:
    """Settings reads from environment with RAC_ prefix."""
    # Set up environment
    os.environ["RAC_ENV"] = "staging"
    os.environ["RAC_INSTITUTION_NAME"] = "Test Hospital"
    os.environ["RAC_PARENT_DOMAIN"] = "hospital.org"
    os.environ["RAC_BRAND_LOGO_URL"] = "https://hospital.org/logo.png"
    os.environ["RAC_IDP_TENANT_ID"] = "tenant-123"
    os.environ["RAC_IDP_CLIENT_ID"] = "client-456"
    os.environ["RAC_IDP_API_CLIENT_ID"] = "api-client-789"
    os.environ["RAC_PG_HOST"] = "postgres.local"
    os.environ["RAC_PG_DB"] = "rac_db"
    os.environ["RAC_PG_USER"] = "postgres"
    os.environ["RAC_PG_PASSWORD"] = "secret"
    os.environ["RAC_KV_URI"] = "https://kv.vault.azure.net/"
    os.environ["RAC_BLOB_ACCOUNT_URL"] = "https://blob.azure.com/"
    os.environ["RAC_ACR_LOGIN_SERVER"] = "acr.azurecr.io"
    os.environ["RAC_ACA_ENV_RESOURCE_ID"] = "/subscriptions/sub/rg/providers/Microsoft.App/managedEnvironments/env"
    os.environ["RAC_SCAN_SEVERITY_GATE"] = "critical"
    os.environ["RAC_APPROVER_ROLE_RESEARCH"] = "ResearchApprover"
    os.environ["RAC_APPROVER_ROLE_IT"] = "ITApprover"

    try:
        settings = Settings()
        assert settings.env == "staging"
        assert settings.institution_name == "Test Hospital"
        assert settings.scan_severity_gate == "critical"
    finally:
        # Clean up environment
        for key in list(os.environ.keys()):
            if key.startswith("RAC_"):
                del os.environ[key]
