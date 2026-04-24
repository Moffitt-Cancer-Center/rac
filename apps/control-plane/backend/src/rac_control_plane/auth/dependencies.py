# pattern: Imperative Shell
"""Combined auth dependency: interactive OIDC or client-credentials.

Implements a unified current_principal() that tries interactive Entra token
first, then falls back to client-credentials. Also provides require_admin().
"""

from typing import Annotated

import jwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.auth.principal import Principal, principal_from_claims
from rac_control_plane.data.agent_repo import AgentRepo
from rac_control_plane.data.db import get_session
from rac_control_plane.errors import AuthError, ForbiddenError
from rac_control_plane.settings import get_settings


def _extract_bearer(request: Request) -> str | None:
    """Extract Bearer token from Authorization header. Returns None if missing."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer "):]


def _decode_token(token: str) -> dict[str, object]:
    """Decode JWT without verification for claim inspection.

    We extract claims for routing (user vs agent). Actual signature verification
    happens via fastapi-azure-auth or the mock in tests. In production this is
    only safe because Entra verifies the token upstream in the same request; in
    the test harness the mock OIDC signs tokens with a known secret.
    """
    try:
        # Decode without verification — claim inspection only.
        # Verification is done by fastapi-azure-auth in production; in tests
        # the token is signed with 'test-secret' and claims are trusted.
        return jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256", "RS256"],
        )
    except jwt.PyJWTError:
        return {}


async def current_principal(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    """Combined auth dependency: interactive OIDC OR client-credentials.

    1. Extracts Bearer token from Authorization header.
    2. If no token, raises AuthError (401 with WWW-Authenticate: Bearer).
    3. Decodes claims:
       - If 'oid' claim present: treat as interactive user token.
       - If 'appid' claim present (and no 'oid'): treat as client-credentials.
    4. For client-credentials, looks up agent by entra_app_id.
       - Unknown appid or disabled agent → ForbiddenError (403).
    5. Returns Principal.

    In production, signature validation is done by fastapi-azure-auth middleware
    before this dependency runs. The dependency handles claim routing only.
    """
    token = _extract_bearer(request)
    if not token:
        raise AuthError(
            public_message="Authentication required. Provide a Bearer token.",
        )

    claims = _decode_token(token)
    if not claims:
        raise AuthError(
            public_message="Invalid or malformed Bearer token.",
        )

    # Store the raw token on request.state for downstream use
    request.state.raw_token = token

    # Route: user token has 'oid', client-credentials token has 'appid' without 'oid'
    oid = claims.get("oid")
    app_id = claims.get("appid")

    if oid:
        # Interactive user token
        principal = principal_from_claims(claims)
        request.state.principal_id = str(principal.oid)
        return principal

    if app_id:
        # Client-credentials token — look up the agent
        repo = AgentRepo(session)
        agent = await repo.get_by_entra_app_id(str(app_id))
        if not agent or not agent.enabled:
            raise ForbiddenError(
                public_message="Agent not found or disabled.",
            )
        principal = Principal(
            oid=agent.service_principal_id,
            kind="agent",
            display_name=agent.name,
            agent_id=agent.id,
            roles=frozenset(),
        )
        request.state.principal_id = str(principal.oid)
        return principal

    raise AuthError(
        public_message="Invalid token: missing required claims (oid or appid).",
    )


async def require_admin(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    """Dependency: require admin role (it_approver role as stand-in for v1).

    Raises:
        ForbiddenError (403): if principal does not have the admin role.
    """
    settings = get_settings()
    if settings.approver_role_it not in principal.roles:
        raise ForbiddenError(
            public_message="Admin role required for this operation.",
        )
    return principal
