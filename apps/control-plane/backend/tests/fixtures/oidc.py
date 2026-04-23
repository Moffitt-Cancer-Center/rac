# pattern: Imperative Shell
"""Test fixture for mock OIDC server.

Provides a mock-oidc container with helpers to issue test tokens.
"""

from uuid import UUID

import jwt
import pytest
from testcontainers.core.container import DockerContainer


@pytest.fixture(scope="session")
def mock_oidc():
    """Session-scoped mock OIDC server via testcontainer.

    Uses ghcr.io/soluto/oidc-server-mock:latest (well-maintained OIDC server).
    Exposes well-known URL for JWT discovery.
    """
    container = DockerContainer(
        "ghcr.io/soluto/oidc-server-mock:latest"
    ).with_exposed_ports(8080)

    container.start()

    # Get the exposed port
    host = container.get_container_host_ip()
    port = container.get_exposed_port(8080)
    base_url = f"http://{host}:{port}"

    # Attach helper methods
    container.base_url = base_url
    container.well_known_url = f"{base_url}/.well-known/openid-configuration"

    # Simple token issuance helpers
    def issue_user_token(oid: UUID, roles: list[str] = None) -> str:
        """Issue a mock OIDC user token with the given oid and roles."""
        if roles is None:
            roles = []
        payload = {
            "oid": str(oid),
            "sub": str(oid),
            "aud": "api://rac-control-plane",
            "roles": roles,
            "name": f"Test User {oid}",
            "preferred_username": f"test-{oid}@example.com",
        }
        # Sign with a test key (in real scenario, the mock server provides keys)
        token = jwt.encode(payload, "test-secret", algorithm="HS256")
        return token

    def issue_client_credentials_token(
        app_id: UUID | str, scopes: list[str] = None
    ) -> str:
        """Issue a mock OAuth2 client-credentials token."""
        if scopes is None:
            scopes = []
        payload = {
            "appid": str(app_id),
            "app_id": str(app_id),
            "aud": "api://rac-control-plane",
            "scope": " ".join(scopes),
        }
        token = jwt.encode(payload, "test-secret", algorithm="HS256")
        return token

    container.issue_user_token = issue_user_token
    container.issue_client_credentials_token = issue_client_credentials_token

    yield container

    container.stop()
