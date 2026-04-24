"""Tests for rac_shim.app_registry.

Uses the testcontainers Postgres fixture from conftest.py.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from rac_shim.app_registry import AppRegistry

ACA_SUFFIX = "internal.test.azurecontainerapps.io"

# DDL for the tables the registry queries
_APP_DDL = """
CREATE TABLE IF NOT EXISTS app (
    id UUID PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    access_mode TEXT NOT NULL DEFAULT 'token_required',
    current_submission_id UUID NULL
);
"""

_SUBMISSION_DDL = """
CREATE TABLE IF NOT EXISTS submission (
    id UUID PRIMARY KEY
);
"""


@pytest.fixture
async def registry_pool(pg_pool: asyncpg.Pool) -> asyncpg.Pool:  # type: ignore[type-arg]
    """Ensure app/submission tables exist and are clean."""
    async with pg_pool.acquire() as conn:
        await conn.execute(_APP_DDL)
        await conn.execute(_SUBMISSION_DDL)
        await conn.execute("TRUNCATE app CASCADE")
        await conn.execute("TRUNCATE submission CASCADE")
    yield pg_pool
    async with pg_pool.acquire() as conn:
        await conn.execute("TRUNCATE app CASCADE")
        await conn.execute("TRUNCATE submission CASCADE")


async def _insert_app(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    slug: str,
    access_mode: str = "token_required",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert an app + submission pair, return (app_id, submission_id)."""
    app_id = uuid.uuid4()
    sub_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO submission (id) VALUES ($1)",
            sub_id,
        )
        await conn.execute(
            "INSERT INTO app (id, slug, access_mode, current_submission_id) VALUES ($1, $2, $3, $4)",
            app_id,
            slug,
            access_mode,
            sub_id,
        )
    return app_id, sub_id


@pytest.mark.asyncio
async def test_registry_get_returns_app_route(registry_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """After refresh, get('slug') returns the correct AppRoute."""
    app_id, _ = await _insert_app(registry_pool, "myapp", "token_required")
    registry = AppRegistry(registry_pool, aca_internal_suffix=ACA_SUFFIX, refresh_interval_seconds=999)
    await registry.start()
    try:
        route = registry.get("myapp")
        assert route is not None
        assert route.slug == "myapp"
        assert route.access_mode == "token_required"
        assert route.upstream_host == f"myapp.{ACA_SUFFIX}"
        assert route.app_id == app_id
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_registry_public_mode(registry_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """access_mode='public' is correctly loaded."""
    await _insert_app(registry_pool, "pubapp", "public")
    registry = AppRegistry(registry_pool, aca_internal_suffix=ACA_SUFFIX, refresh_interval_seconds=999)
    await registry.start()
    try:
        route = registry.get("pubapp")
        assert route is not None
        assert route.access_mode == "public"
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_registry_get_unknown_slug_returns_none(registry_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """get() for an unknown slug returns None."""
    registry = AppRegistry(registry_pool, aca_internal_suffix=ACA_SUFFIX, refresh_interval_seconds=999)
    await registry.start()
    try:
        assert registry.get("no-such-app") is None
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_registry_all_returns_snapshot(registry_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """all() returns a dict snapshot of all routes."""
    await _insert_app(registry_pool, "app-a")
    await _insert_app(registry_pool, "app-b")
    registry = AppRegistry(registry_pool, aca_internal_suffix=ACA_SUFFIX, refresh_interval_seconds=999)
    await registry.start()
    try:
        snapshot = registry.all()
        assert "app-a" in snapshot
        assert "app-b" in snapshot
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_registry_app_without_submission_excluded(registry_pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
    """Apps with current_submission_id=NULL are excluded (JOIN condition)."""
    app_id = uuid.uuid4()
    async with registry_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app (id, slug, access_mode, current_submission_id) VALUES ($1, $2, $3, NULL)",
            app_id,
            "no-sub-app",
            "token_required",
        )
    registry = AppRegistry(registry_pool, aca_internal_suffix=ACA_SUFFIX, refresh_interval_seconds=999)
    await registry.start()
    try:
        assert registry.get("no-sub-app") is None
    finally:
        await registry.stop()
