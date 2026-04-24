"""Tests for rac_shim.token.denylist_cache — revoked-token denylist cache.

Verifies: rac-v1.AC7.2 — revoked tokens are detected within 60 seconds
of insertion (within the TTL window).

Uses a real Postgres testcontainer via the fixtures/db.py fixtures.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from tests.fixtures.db import pg_dsn, pg_pool, postgres_container, truncate_revoked  # noqa: F401
from rac_shim.token.denylist_cache import RevokedTokenDenylistCache


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_denylist_check_false(pg_pool, truncate_revoked) -> None:
    """No rows in revoked_token → check(any jti) returns False."""
    cache = RevokedTokenDenylistCache(pg_pool, ttl_seconds=60)
    jti = uuid.uuid4()
    assert await cache.check(jti) is False


@pytest.mark.asyncio
async def test_insert_revoked_jti_visible_after_refresh(pg_pool, truncate_revoked) -> None:
    """Insert a jti → first check (cache miss) refreshes and returns True."""
    jti = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO revoked_token (jti, expires_at) VALUES ($1, NULL)",
            jti,
        )

    cache = RevokedTokenDenylistCache(pg_pool, ttl_seconds=60)
    assert await cache.check(jti) is True


@pytest.mark.asyncio
async def test_ttl_not_expired_returns_cached(pg_pool, truncate_revoked) -> None:
    """Cache not expired: new jti inserted in DB is NOT visible (cache is stale)."""
    # Populate cache with an initial (empty) snapshot.
    cache = RevokedTokenDenylistCache(pg_pool, ttl_seconds=60)
    known_jti = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO revoked_token (jti, expires_at) VALUES ($1, NULL)",
            known_jti,
        )
    # Prime the cache with the first row visible.
    assert await cache.check(known_jti) is True

    # Now insert a second jti — but the TTL hasn't expired yet.
    new_jti = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO revoked_token (jti, expires_at) VALUES ($1, NULL)",
            new_jti,
        )

    # Still within TTL: new_jti should not be visible.
    assert await cache.check(new_jti) is False


@pytest.mark.asyncio
async def test_ttl_expired_refreshes(pg_pool, truncate_revoked) -> None:
    """Cache with ttl=0.1: after the TTL expires, a new jti becomes visible."""
    cache = RevokedTokenDenylistCache(pg_pool, ttl_seconds=0)

    # First call populates cache (empty at this point).
    jti_before = uuid.uuid4()
    assert await cache.check(jti_before) is False

    # Insert a new jti.
    new_jti = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO revoked_token (jti, expires_at) VALUES ($1, NULL)",
            new_jti,
        )

    # With ttl=0, the cache always re-fetches.
    assert await cache.check(new_jti) is True


@pytest.mark.asyncio
async def test_removed_jti_visible_after_refresh(pg_pool, truncate_revoked) -> None:
    """Insert then delete a jti; after TTL expiry it is no longer seen."""
    jti = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO revoked_token (jti, expires_at) VALUES ($1, NULL)",
            jti,
        )

    # Use ttl=0 so every call re-queries Postgres.
    cache = RevokedTokenDenylistCache(pg_pool, ttl_seconds=0)
    assert await cache.check(jti) is True

    # Delete the row.
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM revoked_token WHERE jti = $1", jti)

    # With ttl=0 the next check re-fetches and the row is gone.
    assert await cache.check(jti) is False


@pytest.mark.asyncio
async def test_expires_at_past_excluded(pg_pool, truncate_revoked) -> None:
    """A row with expires_at in the past is excluded from the denylist query."""
    jti = uuid.uuid4()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO revoked_token (jti, expires_at) VALUES ($1, $2)",
            jti,
            past,
        )

    cache = RevokedTokenDenylistCache(pg_pool, ttl_seconds=0)
    # Row exists but expires_at is in the past — should NOT appear in denylist.
    assert await cache.check(jti) is False
