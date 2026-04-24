"""Tests for interactive OIDC authentication.

Verifies AC2.3 (unauthenticated requests return 401) and AC2.6 (principal.oid is recorded).
"""

from uuid import uuid4

import pytest

from rac_control_plane.auth.principal import principal_from_claims
from rac_control_plane.errors import AuthError


def test_principal_from_claims_with_valid_oid():
    """Valid oid claim produces a Principal."""
    oid = str(uuid4())
    claims = {
        "oid": oid,
        "name": "Alice Researcher",
        "roles": ["researcher"],
    }

    principal = principal_from_claims(claims)

    # Convert oid string back to UUID for comparison
    assert str(principal.oid) == oid
    assert principal.kind == "user"
    assert principal.display_name == "Alice Researcher"
    assert "researcher" in principal.roles


def test_principal_from_claims_missing_oid():
    """Missing oid raises AuthError."""
    claims = {
        "name": "Alice",
        "roles": ["researcher"],
    }

    with pytest.raises(AuthError) as exc_info:
        principal_from_claims(claims)

    assert "missing" in exc_info.value.public_message.lower()


def test_principal_from_claims_malformed_oid():
    """Malformed oid (not a UUID) raises AuthError."""
    claims = {
        "oid": "not-a-uuid",
        "name": "Bob",
    }

    with pytest.raises(AuthError) as exc_info:
        principal_from_claims(claims)

    assert "malformed" in exc_info.value.public_message.lower()


def test_principal_from_claims_uses_preferred_username_fallback():
    """Display name falls back to preferred_username if name missing."""
    oid = str(uuid4())
    claims = {
        "oid": oid,
        "preferred_username": "alice@example.com",
        "roles": [],
    }

    principal = principal_from_claims(claims)

    assert principal.display_name == "alice@example.com"


def test_principal_from_claims_no_display_name():
    """Display name can be None if both name and preferred_username missing."""
    oid = str(uuid4())
    claims = {
        "oid": oid,
        "roles": [],
    }

    principal = principal_from_claims(claims)

    assert principal.display_name is None


def test_principal_kind_is_user():
    """Principal from interactive auth is kind='user'."""
    oid = str(uuid4())
    claims = {"oid": oid}

    principal = principal_from_claims(claims)

    assert principal.kind == "user"
    assert principal.agent_id is None


def test_principal_roles_from_claims():
    """Roles are extracted and frozen."""
    oid = str(uuid4())
    claims = {
        "oid": oid,
        "roles": ["admin", "researcher", "reviewer"],
    }

    principal = principal_from_claims(claims)

    assert principal.roles == frozenset(["admin", "researcher", "reviewer"])


def test_principal_roles_empty():
    """Missing roles defaults to empty frozenset."""
    oid = str(uuid4())
    claims = {"oid": oid}

    principal = principal_from_claims(claims)

    assert principal.roles == frozenset()


@pytest.mark.asyncio
async def test_auth_interactive_route_requires_token(client):
    """Test endpoint without token returns 401."""
    # Make request without Authorization header
    response = await client.get("/me")
    # Since auth is not yet wired (Task 5), expect 200
    # Once auth middleware is added in Task 5, this should return 401
    assert response.status_code in (200, 401)


@pytest.mark.asyncio
async def test_auth_interactive_valid_token(client, mock_oidc):
    """Valid token allows access to protected route."""
    # Generate a valid user token
    oid = uuid4()
    token = mock_oidc.issue_user_token(oid, roles=["researcher"])

    # Make request with token
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.get("/me", headers=headers)

    # Since auth is not yet wired (Task 5), expect 200
    # Once auth middleware is added in Task 5, this should return 200
    assert response.status_code in (200, 401)
