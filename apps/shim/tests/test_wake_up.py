"""Tests for rac_shim.proxy.wake_up."""
from __future__ import annotations

import httpx
import pytest
import respx

from rac_shim.proxy.wake_up import wake

UPSTREAM_HOST = "upstream.internal"


@pytest.mark.asyncio
async def test_wake_success_returns_ms() -> None:
    """Successful wake returns a positive float (elapsed ms)."""
    router = respx.MockRouter()
    router.get(f"http://{UPSTREAM_HOST}/").mock(return_value=httpx.Response(200, content=b"ok"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(router.handler)) as client:
        result = await wake(UPSTREAM_HOST, client=client)
    assert result is not None
    assert result > 0


@pytest.mark.asyncio
async def test_wake_timeout_returns_none() -> None:
    """Wake returns None when the upstream times out."""

    async def _slow_handler(request: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(10)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_slow_handler)) as client:
        result = await wake(UPSTREAM_HOST, client=client, timeout_seconds=0.01)
    assert result is None


@pytest.mark.asyncio
async def test_wake_network_error_returns_none() -> None:
    """Wake returns None on a transport error."""
    router = respx.MockRouter()
    router.get(f"http://{UPSTREAM_HOST}/").mock(
        side_effect=httpx.TransportError("network down")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(router.handler)) as client:
        result = await wake(UPSTREAM_HOST, client=client)
    assert result is None
