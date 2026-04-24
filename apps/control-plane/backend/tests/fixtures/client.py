# pattern: Imperative Shell
"""Test fixture for FastAPI test client.

Provides an httpx AsyncClient configured with test settings and middleware.
"""

from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from rac_control_plane.data.db import get_engine, get_session_maker
from rac_control_plane.main import create_app
from rac_control_plane.settings import get_settings


def _parse_dsn(dsn: str) -> dict:  # type: ignore[type-arg]
    """Parse PostgreSQL DSN into components."""
    dsn_to_parse = dsn.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(dsn_to_parse)

    netloc = parsed.netloc
    if "@" in netloc:
        auth, host_part = netloc.rsplit("@", 1)
        if ":" in auth:
            user, password = auth.split(":", 1)
        else:
            user = auth
            password = ""
    else:
        user = ""
        password = ""
        host_part = netloc

    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        port = port_str
    else:
        host = host_part
        port = "5432"

    db = parsed.path.lstrip("/").split("?")[0]

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "db": db,
    }


@pytest.fixture
async def app(monkeypatch, migrated_db, mock_oidc):
    """Function-scoped: creates FastAPI app with test settings.

    Overrides environment variables to point to test Postgres and mock OIDC.
    """
    # Clear settings cache so new environment is picked up
    get_settings.cache_clear()

    # Parse the DSN
    dsn_parts = _parse_dsn(migrated_db)

    # Set test environment variables
    test_env = {
        "RAC_ENV": "dev",
        "RAC_INSTITUTION_NAME": "Test Institution",
        "RAC_PARENT_DOMAIN": "test.local",
        "RAC_BRAND_LOGO_URL": "https://example.com/logo.png",
        "RAC_IDP_TENANT_ID": "test-tenant",
        "RAC_IDP_CLIENT_ID": "test-client",
        "RAC_IDP_API_CLIENT_ID": "test-api-client",
        "RAC_PG_HOST": dsn_parts["host"],
        "RAC_PG_PORT": dsn_parts["port"],
        "RAC_PG_DB": dsn_parts["db"],
        "RAC_PG_USER": dsn_parts["user"],
        "RAC_PG_PASSWORD": dsn_parts["password"],
        "RAC_PG_SSL_MODE": "disable",
        "RAC_KV_URI": "https://test-kv.vault.azure.net/",
        "RAC_BLOB_ACCOUNT_URL": "https://teststorage.blob.core.windows.net/",
        "RAC_ACR_LOGIN_SERVER": "test.azurecr.io",
        "RAC_ACA_ENV_RESOURCE_ID": (
            "/subscriptions/test/resourceGroups/test"
            "/providers/Microsoft.App/managedEnvironments/test"
        ),
        "RAC_SCAN_SEVERITY_GATE": "high",
        "RAC_APPROVER_ROLE_RESEARCH": "research_approver",
        "RAC_APPROVER_ROLE_IT": "it_approver",
        "RAC_OTLP_ENDPOINT": "http://localhost:4317",
    }

    for key, value in test_env.items():
        monkeypatch.setenv(key, value)

    # Clear all caches after setting env vars so app picks up new DB settings
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_maker.cache_clear()

    # Create the app
    app_instance = create_app()

    yield app_instance

    # Clean up engine/session caches so subsequent tests get fresh instances
    get_engine.cache_clear()
    get_session_maker.cache_clear()


@pytest.fixture
async def client(app):
    """Function-scoped: provides an httpx AsyncClient for the test app.

    Uses ASGITransport to call the app directly without HTTP overhead.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client_instance:
        yield client_instance
