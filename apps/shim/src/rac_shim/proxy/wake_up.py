# pattern: Imperative Shell
"""Fire-and-don't-wait wake call to nudge ACA scale-from-zero."""
from __future__ import annotations

import asyncio
import time

import httpx


async def wake(
    upstream_host: str,
    *,
    client: httpx.AsyncClient,
    path: str = "/",
    timeout_seconds: float = 20.0,
) -> float | None:
    """Make a single HTTP GET to http://{upstream_host}{path}.

    Returns wall-clock milliseconds to first response, or None on
    timeout/failure.

    Intended to be scheduled as asyncio.create_task() from the main handler
    while an interstitial is served to the user (AC6.2).
    """
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            resp = await client.get(f"http://{upstream_host}{path}")
            elapsed_ms = (time.monotonic() - started) * 1000
            _ = resp  # response body is intentionally ignored
            return elapsed_ms
    except (httpx.HTTPError, OSError, TimeoutError):
        return None
