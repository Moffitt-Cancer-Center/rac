# pattern: Imperative Shell
"""Denylist (revoked_token table) cache with 60-second TTL.

Verifies: rac-v1.AC7.2 — revoked tokens are detected within 60 seconds of
revocation (the cache TTL provides the upper bound on staleness).
"""
from __future__ import annotations

import asyncio
import time
from uuid import UUID

from asyncpg import Pool


class RevokedTokenDenylistCache:
    """In-memory cache of revoked JTIs backed by the ``revoked_token`` Postgres table.

    The cache is a single frozenset that is refreshed at most once per
    ``ttl_seconds``.  Double-checked locking prevents thundering-herd refreshes.
    """

    def __init__(self, pg_pool: Pool, *, ttl_seconds: int = 60) -> None:
        self._pool = pg_pool
        self._ttl = ttl_seconds
        self._cache: frozenset[UUID] = frozenset()
        self._cache_expires_at: float = 0.0  # monotonic timestamp
        self._lock = asyncio.Lock()

    async def current_denylist(self) -> frozenset[UUID]:
        """Return the cached denylist, refreshing from Postgres if the TTL has expired."""
        now = time.monotonic()
        if self._cache_expires_at > now:
            return self._cache

        async with self._lock:
            now = time.monotonic()
            if self._cache_expires_at > now:
                return self._cache

            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT jti FROM revoked_token "
                    "WHERE expires_at IS NULL OR expires_at > NOW()"
                )
            self._cache = frozenset(row["jti"] for row in rows)
            self._cache_expires_at = now + self._ttl
            return self._cache

    async def check(self, jti: UUID) -> bool:
        """Return True if ``jti`` is in the current denylist (cache-aware)."""
        denylist = await self.current_denylist()
        return jti in denylist
