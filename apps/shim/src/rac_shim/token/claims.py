"""RacTokenClaims: shape of a validated reviewer token. Type-only."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class RacTokenClaims:
    iss: str
    aud: str  # format "rac-app:{slug}"
    sub: str  # reviewer label
    jti: UUID
    iat: datetime
    exp: datetime
    nbf: datetime | None = None
    scope: str | None = "read"
