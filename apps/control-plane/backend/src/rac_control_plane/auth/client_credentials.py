# pattern: Imperative Shell
"""Client-credentials authentication for service-to-service access.

Uses fastapi-azure-auth to validate bearer tokens from Entra,
looks up the agent in the database, and returns a Principal
with kind='agent' and the agent's database ID.

Note: This module is superseded by auth/dependencies.py which provides
the combined current_principal() dependency used by route handlers.
It is kept for backward compatibility and documentation purposes.
"""

from typing import Annotated

from fastapi import Depends

from rac_control_plane.auth.entra import _get_azure_auth
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.agent_repo import AgentRepo
from rac_control_plane.errors import ForbiddenError


async def current_principal_client_credentials(
    auth_claims: Annotated[dict[str, object], Depends(_get_azure_auth())],
    agent_repo: Annotated[AgentRepo, Depends()],
) -> Principal:
    """FastAPI dependency: extract Principal from client-credentials token.

    Looks up the agent by entra_app_id (appid claim) and returns a
    Principal with kind='agent', agent_id set, and oid=agent.service_principal_id.

    Raises:
        ForbiddenError (403): If agent not found or disabled.
    """
    # Extract appid from token (client-credentials tokens use appid instead of oid)
    app_id = auth_claims.get("appid")
    if not app_id:
        raise ForbiddenError(
            public_message="Invalid token: missing appid claim.",
        )

    # Look up agent by entra_app_id
    agent = await agent_repo.get_by_entra_app_id(str(app_id))
    if not agent or not agent.enabled:
        raise ForbiddenError(
            public_message="Agent not found or disabled.",
        )

    # Return Principal with agent details
    return Principal(
        oid=agent.service_principal_id,
        kind="agent",
        display_name=agent.name,
        agent_id=agent.id,
        roles=frozenset(),
    )
