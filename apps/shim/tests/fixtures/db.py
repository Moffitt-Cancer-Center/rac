# pattern: Imperative Shell
"""Asyncpg-based Postgres testcontainer fixtures for the shim test suite.

Uses the pre-built ``rac-pg-uuidv7:test`` image (same image as the control-plane).
The shim uses asyncpg directly (not SQLAlchemy) so these fixtures expose
a Pool rather than a SQLAlchemy engine.
"""
from __future__ import annotations

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

# ---------------------------------------------------------------------------
# DDL for shim-owned tables (shim doesn't run Alembic migrations)
# ---------------------------------------------------------------------------

_REVOKED_TOKEN_DDL = """
CREATE TABLE IF NOT EXISTS revoked_token (
    jti UUID PRIMARY KEY,
    expires_at TIMESTAMPTZ NULL
);
"""

_ACCESS_LOG_DDL = """
CREATE TABLE IF NOT EXISTS access_log (
    id UUID PRIMARY KEY,
    app_id UUID NOT NULL,
    submission_id UUID NULL,
    reviewer_token_jti UUID NULL,
    access_mode TEXT NOT NULL,
    host TEXT NOT NULL,
    path TEXT NOT NULL,
    method TEXT NOT NULL,
    upstream_status INT NULL,
    latency_ms INT NOT NULL,
    user_agent TEXT NULL,
    source_ip TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    request_id UUID NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Container + DSN (session-scoped — start once, shared across all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container():
    """Session-scoped Postgres container (rac-pg-uuidv7:test image)."""
    container = PostgresContainer(image="rac-pg-uuidv7:test")
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def pg_dsn(postgres_container) -> str:  # type: ignore[no-untyped-def]
    """Session-scoped asyncpg DSN derived from the testcontainer URL."""
    url = postgres_container.get_connection_url()
    # testcontainers returns a psycopg2-style URL; rewrite to plain asyncpg form.
    dsn = (
        url.replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )
    return dsn


# ---------------------------------------------------------------------------
# Pool fixture (function-scoped async)
# ---------------------------------------------------------------------------


@pytest.fixture
async def pg_pool(pg_dsn: str) -> asyncpg.Pool:  # type: ignore[type-arg]
    """Function-scoped asyncpg Pool.

    Ensures both test tables exist before yielding.
    Closes the pool after the test completes.
    """
    pool: asyncpg.Pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=5)  # type: ignore[type-arg]
    async with pool.acquire() as conn:
        await conn.execute(_REVOKED_TOKEN_DDL)
        await conn.execute(_ACCESS_LOG_DDL)
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Truncation helpers (function-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture
async def truncate_revoked(pg_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """Truncate the revoked_token table before and after each test."""
    async with pg_pool.acquire() as conn:
        await conn.execute("TRUNCATE revoked_token")
    yield  # type: ignore[misc]
    async with pg_pool.acquire() as conn:
        await conn.execute("TRUNCATE revoked_token")


@pytest.fixture
async def truncate_access_log(pg_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """Truncate the access_log table before and after each test."""
    async with pg_pool.acquire() as conn:
        await conn.execute("TRUNCATE access_log")
    yield  # type: ignore[misc]
    async with pg_pool.acquire() as conn:
        await conn.execute("TRUNCATE access_log")
