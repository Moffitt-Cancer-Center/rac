"""Helper: creates a test Settings object for provisioning wrapper tests."""

from rac_control_plane.settings import Settings


def make_test_settings() -> Settings:
    """Create a Settings object suitable for unit-testing provisioning wrappers."""
    return Settings(
        env="dev",
        institution_name="Test Institution",
        parent_domain="test.local",
        brand_logo_url="https://example.com/logo.png",
        idp_tenant_id="tenant-id",
        idp_client_id="client-id",
        idp_api_client_id="api-client-id",
        pg_host="localhost",
        pg_db="testdb",
        pg_user="user",
        pg_password="password",
        pg_ssl_mode="disable",
        kv_uri="https://test-kv.vault.azure.net/",
        blob_account_url="https://teststorage.blob.core.windows.net/",
        acr_login_server="test.azurecr.io",
        aca_env_resource_id=(
            "/subscriptions/test/resourceGroups/test"
            "/providers/Microsoft.App/managedEnvironments/test"
        ),
        scan_severity_gate="high",
        approver_role_research="research_approver",
        approver_role_it="it_approver",
        # Phase 5 fields
        subscription_id="test-sub-id",
        resource_group="rg-rac-tier3-dev",
        azure_location="eastus",
        dns_zone_name="rac.test.local",
        files_storage_account_name="stracdev",
        managed_identity_resource_id="/subscriptions/test/resourceGroups/test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-rac",
        controlplane_managed_identity_client_id="test-mi-client-id",
        app_gateway_public_ip="10.0.0.1",
        # Phase 7 fields
        max_reviewer_token_ttl_days=180,
        issuer="https://rac.test.local",
        require_publication_for_public=False,
    )
