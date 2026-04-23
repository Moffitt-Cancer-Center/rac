"""Integration tests for FastAPI app health and error handling."""
import os

import pytest
from starlette.testclient import TestClient

from rac_control_plane.main import app
from rac_control_plane.settings import get_settings


def _setup_env() -> None:
    """Set up test environment variables."""
    os.environ["RAC_ENV"] = "dev"
    os.environ["RAC_INSTITUTION_NAME"] = "Test"
    os.environ["RAC_PARENT_DOMAIN"] = "example.com"
    os.environ["RAC_BRAND_LOGO_URL"] = "https://example.com/logo.png"
    os.environ["RAC_IDP_TENANT_ID"] = "tenant"
    os.environ["RAC_IDP_CLIENT_ID"] = "client"
    os.environ["RAC_IDP_API_CLIENT_ID"] = "api-client"
    os.environ["RAC_PG_HOST"] = "localhost"
    os.environ["RAC_PG_DB"] = "testdb"
    os.environ["RAC_PG_USER"] = "user"
    os.environ["RAC_PG_PASSWORD"] = "password"
    os.environ["RAC_KV_URI"] = "https://kv.vault.azure.net/"
    os.environ["RAC_BLOB_ACCOUNT_URL"] = "https://blob.azure.com/"
    os.environ["RAC_ACR_LOGIN_SERVER"] = "acr.azurecr.io"
    os.environ["RAC_ACA_ENV_RESOURCE_ID"] = "/subscriptions/sub/rg/providers/Microsoft.App/managedEnvironments/env"
    os.environ["RAC_SCAN_SEVERITY_GATE"] = "high"
    os.environ["RAC_APPROVER_ROLE_RESEARCH"] = "ResearchApprover"
    os.environ["RAC_APPROVER_ROLE_IT"] = "ITApprover"


def _cleanup_env() -> None:
    """Clean up test environment variables."""
    for key in list(os.environ.keys()):
        if key.startswith("RAC_"):
            del os.environ[key]
    get_settings.cache_clear()


def test_health_check_returns_200() -> None:
    """GET /health returns 200 with expected structure."""
    _setup_env()
    try:
        get_settings.cache_clear()

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["version"] == "1.0.0"
        assert body["env"] == "dev"
    finally:
        _cleanup_env()


def test_correlation_id_echoed_in_header() -> None:
    """X-Request-Id is echoed back in response headers."""
    _setup_env()
    try:
        get_settings.cache_clear()

        client = TestClient(app)
        test_id = "test-correlation-id-12345"
        response = client.get(
            "/health",
            headers={"X-Request-Id": test_id},
        )

        # Should echo back the request ID
        assert response.headers.get("x-request-id") == test_id
    finally:
        _cleanup_env()


def test_correlation_id_generated_if_missing() -> None:
    """Correlation ID is generated if not provided."""
    _setup_env()
    try:
        get_settings.cache_clear()

        client = TestClient(app)
        response = client.get("/health")

        # Should have x-request-id header even without input
        assert "x-request-id" in response.headers
        request_id = response.headers["x-request-id"]
        assert len(request_id) > 0
    finally:
        _cleanup_env()
