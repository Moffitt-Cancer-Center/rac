"""Tests for rac_shim.proxy.forward (AC7.1, AC7.5, AC6.2)."""
from __future__ import annotations

import httpx
import pytest
import respx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from rac_shim.proxy.forward import proxy_request

UPSTREAM_HOST = "upstream.internal"
APP_SLUG = "test-app"


# ---------------------------------------------------------------------------
# Helper: build a Starlette app that proxies via a given respx router
# ---------------------------------------------------------------------------


def _make_handler(
    router: respx.MockRouter,
    *,
    reviewer_label: str | None = None,
    reviewer_jti: str | None = None,
    timeout_seconds: float = 30.0,
):  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(router.handler)

    async def handler(request: Request) -> Response:
        async with httpx.AsyncClient(transport=transport) as client:
            return await proxy_request(
                request,
                upstream_host=UPSTREAM_HOST,
                reviewer_label=reviewer_label,
                reviewer_jti=reviewer_jti,
                app_slug=APP_SLUG,
                client=client,
                timeout_seconds=timeout_seconds,
            )

    return handler


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_forward_preserves_method_and_body() -> None:
    """POST with a body: upstream saw the same method and body."""
    router = respx.MockRouter()
    route = router.post(f"http://{UPSTREAM_HOST}/submit").mock(
        return_value=httpx.Response(200, content=b"ok")
    )
    handler = _make_handler(router)
    app = Starlette(routes=[Route("/submit", handler, methods=["POST"])])
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/submit", content=b"hello-body", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    assert route.called
    sent_request = route.calls.last.request
    assert sent_request.method == "POST"
    assert sent_request.content == b"hello-body"


def test_strips_authorization_and_cookie() -> None:
    """Headers Authorization and Cookie are not forwarded upstream."""
    router = respx.MockRouter()
    route = router.get(f"http://{UPSTREAM_HOST}/page").mock(
        return_value=httpx.Response(200, content=b"ok")
    )
    handler = _make_handler(router)
    app = Starlette(routes=[Route("/page", handler)])
    client = TestClient(app)
    resp = client.get(
        "/page",
        headers={
            "Authorization": "Bearer supersecret",
            "Cookie": "session=abc123",
        },
    )
    assert resp.status_code == 200
    sent = route.calls.last.request
    assert "authorization" not in {k.lower() for k in sent.headers}
    assert "cookie" not in {k.lower() for k in sent.headers}


def test_strips_hop_by_hop() -> None:
    """Connection hop-by-hop header is not forwarded upstream."""
    router = respx.MockRouter()
    route = router.get(f"http://{UPSTREAM_HOST}/").mock(
        return_value=httpx.Response(200, content=b"ok")
    )
    handler = _make_handler(router)
    app = Starlette(routes=[Route("/", handler)])
    client = TestClient(app)
    resp = client.get("/", headers={"Connection": "keep-alive"})
    assert resp.status_code == 200
    sent = route.calls.last.request
    assert "connection" not in {k.lower() for k in sent.headers}


def test_adds_xrac_headers_token_required() -> None:
    """X-RAC-App-Slug, X-RAC-Reviewer-Label, X-RAC-Reviewer-Jti sent upstream (AC7.1)."""
    router = respx.MockRouter()
    route = router.get(f"http://{UPSTREAM_HOST}/data").mock(
        return_value=httpx.Response(200, content=b"ok")
    )
    handler = _make_handler(router, reviewer_label="reviewer-1", reviewer_jti="some-uuid")
    app = Starlette(routes=[Route("/data", handler)])
    client = TestClient(app)
    resp = client.get("/data")
    assert resp.status_code == 200
    sent = route.calls.last.request
    sent_lower = {k.lower(): v for k, v in sent.headers.items()}
    assert sent_lower.get("x-rac-app-slug") == APP_SLUG
    assert sent_lower.get("x-rac-reviewer-label") == "reviewer-1"
    assert sent_lower.get("x-rac-reviewer-jti") == "some-uuid"


def test_public_mode_no_reviewer_headers() -> None:
    """In public mode (reviewer_label=None, jti=None), no X-RAC-Reviewer-* headers (AC7.5)."""
    router = respx.MockRouter()
    route = router.get(f"http://{UPSTREAM_HOST}/public").mock(
        return_value=httpx.Response(200, content=b"ok")
    )
    handler = _make_handler(router, reviewer_label=None, reviewer_jti=None)
    app = Starlette(routes=[Route("/public", handler)])
    client = TestClient(app)
    resp = client.get("/public")
    assert resp.status_code == 200
    sent = route.calls.last.request
    sent_lower = {k.lower() for k in sent.headers}
    assert "x-rac-reviewer-label" not in sent_lower
    assert "x-rac-reviewer-jti" not in sent_lower
    assert "x-rac-app-slug" in sent_lower


def test_streams_response_body() -> None:
    """Upstream body is fully relayed to the client."""
    large_body = b"x" * 100_000
    router = respx.MockRouter()
    router.get(f"http://{UPSTREAM_HOST}/large").mock(
        return_value=httpx.Response(200, content=large_body)
    )
    handler = _make_handler(router)
    app = Starlette(routes=[Route("/large", handler)])
    client = TestClient(app)
    resp = client.get("/large")
    assert resp.status_code == 200
    assert resp.content == large_body


def test_upstream_timeout_returns_502() -> None:
    """TimeoutException from upstream returns a 502 sentinel response (AC6.2)."""
    router = respx.MockRouter()
    router.get(f"http://{UPSTREAM_HOST}/slow").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    handler = _make_handler(router, timeout_seconds=0.001)
    app = Starlette(routes=[Route("/slow", handler)])
    client = TestClient(app)
    resp = client.get("/slow")
    assert resp.status_code == 502


def test_status_code_preserved() -> None:
    """Upstream 201 is preserved in the proxy response."""
    router = respx.MockRouter()
    router.post(f"http://{UPSTREAM_HOST}/create").mock(
        return_value=httpx.Response(201, content=b"created")
    )
    handler = _make_handler(router)
    app = Starlette(routes=[Route("/create", handler, methods=["POST"])])
    client = TestClient(app)
    resp = client.post("/create")
    assert resp.status_code == 201
