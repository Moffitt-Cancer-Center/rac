# pattern: Imperative Shell
"""Periodically refreshes a dict[slug, AppRoute] from the app + submission tables."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import structlog

from rac_shim.routing.decision import AppRoute

if TYPE_CHECKING:
    import asyncpg

log: structlog.BoundLogger = structlog.get_logger(__name__)

_REFRESH_QUERY = """
    SELECT
        a.slug,
        a.id        AS app_id,
        a.access_mode,
        s.id        AS submission_id
    FROM app a
    JOIN submission s ON s.id = a.current_submission_id
    WHERE a.current_submission_id IS NOT NULL
"""


class AppRegistry:
    """In-memory cache of app routes, refreshed from Postgres on an interval."""

    def __init__(
        self,
        pg_pool: "asyncpg.Pool[asyncpg.Record]",
        *,
        aca_internal_suffix: str,
        refresh_interval_seconds: int = 30,
    ) -> None:
        self._pool = pg_pool
        self._aca_suffix = aca_internal_suffix
        self._interval = refresh_interval_seconds
        self._routes: dict[str, AppRoute] = {}
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Perform an initial refresh then schedule the periodic refresh loop."""
        await self._refresh()
        self._task = asyncio.create_task(self._loop(), name="app_registry_refresh")

    async def stop(self) -> None:
        """Cancel the background refresh task and wait for it to finish."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def get(self, slug: str) -> AppRoute | None:
        """Return the AppRoute for the given slug, or None if not found."""
        return self._routes.get(slug)

    def all(self) -> dict[str, AppRoute]:
        """Return a snapshot of the current route table."""
        return dict(self._routes)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Background coroutine: refresh every _interval seconds."""
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._refresh()
            except Exception:
                log.exception("app_registry_refresh_error")

    async def _refresh(self) -> None:
        """Query the DB and rebuild the in-memory route table."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_REFRESH_QUERY)

        new_routes: dict[str, AppRoute] = {}
        for row in rows:
            slug: str = row["slug"]
            upstream_host = f"{slug}.{self._aca_suffix}"
            new_routes[slug] = AppRoute(
                slug=slug,
                app_id=row["app_id"],
                upstream_host=upstream_host,
                access_mode=row["access_mode"],
            )

        self._routes = new_routes
        log.debug("app_registry_refreshed", count=len(new_routes))
