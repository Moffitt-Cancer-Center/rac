# pattern: Imperative Shell
"""Interactive OIDC authentication via Entra (Azure AD).

Uses fastapi-azure-auth for token validation and claims extraction.
"""

from typing import Annotated

from fastapi import Depends
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer

from rac_control_plane.auth.principal import Principal, principal_from_claims
from rac_control_plane.settings import get_settings


def _get_azure_auth():
    """Create and cache the Azure auth scheme."""
    settings = get_settings()
    return SingleTenantAzureAuthorizationCodeBearer(
        app_client_id=settings.idp_api_client_id,
        tenant_id=settings.idp_tenant_id,
        scopes={"api://rac-control-plane/submit": "Submit applications"},
        allow_guest_users=False,
    )


async def current_principal_interactive(
    auth_claims: Annotated[dict, Depends(_get_azure_auth())]
) -> Principal:
    """FastAPI dependency: extract Principal from interactive OIDC token.

    Raises:
        AuthError: If token is invalid or claims are missing.
    """
    return principal_from_claims(auth_claims)
