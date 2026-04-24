# pattern: Functional Core
"""Pure reviewer-token claim builder.

No I/O, no side effects. Takes typed inputs and returns a plain dict.
"""

from datetime import datetime
from uuid import UUID


def build_reviewer_claims(
    *,
    app_slug: str,
    reviewer_label: str,
    issuer: str,
    issued_at: datetime,
    expires_at: datetime,
    jti: UUID,
    scope: str = "read",
) -> dict[str, object]:
    """Build the JWT claim set for a reviewer token.

    Returns a dict with exactly 7 fields:
        iss, aud, sub, jti, iat, exp, scope

    Args:
        app_slug: Slug of the app being accessed (becomes aud).
        reviewer_label: Human label chosen by the researcher (becomes sub).
        issuer: JWT iss claim — the Control Plane's public URL.
        issued_at: Token issuance time.
        expires_at: Token expiry time.
        jti: Unique token identifier (UUID).
        scope: Permission scope (default "read").

    Returns:
        Claim dict ready for JWS encoding.
    """
    return {
        "iss": issuer,
        "aud": f"rac-app:{app_slug}",
        "sub": reviewer_label,
        "jti": str(jti),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "scope": scope,
    }
