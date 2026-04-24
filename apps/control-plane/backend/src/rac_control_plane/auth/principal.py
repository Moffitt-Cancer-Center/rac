# pattern: Functional Core
"""Pure Principal type and claim-to-principal mapping.

This module is purely functional: it takes claims dicts and produces
Principal instances, with validation but no I/O or side effects.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from rac_control_plane.errors import AuthError


@dataclass(frozen=True)
class Principal:
    """Authenticated principal (user or agent).

    Attributes:
        oid: Entra object ID (for users) or service principal ID (for agents).
        kind: Type of principal: 'user' or 'agent'.
        display_name: Human-readable name (optional).
        agent_id: If kind='agent', the agent's DB ID (optional).
        roles: Set of role names (e.g., 'admin', 'researcher', 'reviewer').
    """

    oid: UUID
    kind: Literal["user", "agent"]
    display_name: str | None = None
    agent_id: UUID | None = None
    roles: frozenset[str] = frozenset()


def principal_from_claims(claims: dict[str, object]) -> Principal:
    """Pure mapping from OIDC/Azure claims to Principal.

    Arguments:
        claims: Dict from token claims (from fastapi-azure-auth or similar).

    Returns:
        Principal instance.

    Raises:
        AuthError: If required claims are missing or malformed.
    """
    # Extract OID (object ID for users, will be service principal ID for agents)
    oid_str = str(claims["oid"]) if "oid" in claims else None
    if not oid_str:
        raise AuthError(
            public_message="Invalid token: missing object ID (oid) claim.",
        )

    try:
        oid = UUID(oid_str)
    except (ValueError, TypeError) as e:
        raise AuthError(
            public_message="Invalid token: malformed object ID.",
        ) from e

    # Display name (optional)
    name_val = claims.get("name") or claims.get("preferred_username")
    display_name = str(name_val) if name_val is not None else None

    # Roles from token (typically in 'roles' claim for Entra)
    raw_roles = claims.get("roles", [])
    if isinstance(raw_roles, str):
        roles_list: list[str] = [raw_roles]
    elif isinstance(raw_roles, list):
        roles_list = [str(r) for r in raw_roles]
    else:
        roles_list = []
    roles = frozenset(roles_list)

    # For now, assume user (no agent_id). Agents are handled separately.
    return Principal(
        oid=oid,
        kind="user",
        display_name=display_name,
        agent_id=None,
        roles=roles,
    )
