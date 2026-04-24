# pattern: Functional Core

from uuid import UUID


def is_revoked(jti: UUID, denylist: frozenset[UUID]) -> bool:
    return jti in denylist
