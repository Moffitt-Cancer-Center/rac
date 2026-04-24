# pattern: Imperative Shell
"""Streaming reverse proxy from Starlette Request to an upstream HTTP service."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

HOP_BY_HOP_HEADERS = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)

# Headers the shim strips unconditionally before forwarding.
_STRIP_ALWAYS = frozenset(["host", "authorization", "cookie"])


def _headers_for_upstream(
    request: Request,
    *,
    reviewer_label: str | None,
    reviewer_jti: str | None,
    app_slug: str,
) -> dict[str, str]:
    """Build the header dict to send upstream.

    Rules:
    - Copy all request headers EXCEPT host, authorization, cookie, and hop-by-hop.
    - Always add X-RAC-App-Slug.
    - Add X-RAC-Reviewer-Label if reviewer_label is not None.
    - Add X-RAC-Reviewer-Jti if reviewer_jti is not None.
    """
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lname = name.lower()
        if lname in _STRIP_ALWAYS or lname in HOP_BY_HOP_HEADERS:
            continue
        headers[name] = value

    headers["X-RAC-App-Slug"] = app_slug
    if reviewer_label is not None:
        headers["X-RAC-Reviewer-Label"] = reviewer_label
    if reviewer_jti is not None:
        headers["X-RAC-Reviewer-Jti"] = reviewer_jti

    return headers


async def proxy_request(
    request: Request,
    *,
    upstream_host: str,
    reviewer_label: str | None,
    reviewer_jti: str | None,
    app_slug: str,
    client: httpx.AsyncClient,
    timeout_seconds: float = 30.0,
) -> Response:
    """Proxy the request to upstream and return a StreamingResponse.

    On httpx.TimeoutException / httpx.TransportError / httpx.NetworkError:
    returns a sentinel Response with status_code=502 so the caller can decide
    whether to serve the cold-start interstitial.

    All other exceptions propagate to the caller.
    """
    # Build upstream URL: preserve path + query string.
    qs = request.url.query
    upstream_url = f"http://{upstream_host}{request.url.path}"
    if qs:
        upstream_url = f"{upstream_url}?{qs}"

    upstream_headers = _headers_for_upstream(
        request,
        reviewer_label=reviewer_label,
        reviewer_jti=reviewer_jti,
        app_slug=app_slug,
    )

    try:
        # We need to consume the request body first so we can replay it.
        body = await request.body()

        async def _iter_upstream() -> AsyncIterator[bytes]:
            async with client.stream(
                request.method,
                upstream_url,
                headers=upstream_headers,
                content=body,
                timeout=timeout_seconds,
                follow_redirects=False,
            ) as upstream_resp:
                # Collect and yield in a single context-managed block.
                # We capture status + headers outside the generator because
                # StreamingResponse needs them at construction time.
                _iter_upstream._status_code = upstream_resp.status_code  # type: ignore[attr-defined]
                _iter_upstream._response_headers = dict(upstream_resp.headers)  # type: ignore[attr-defined]
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk

        # We cannot call StreamingResponse without knowing status/headers first,
        # so we do a non-streaming send for the header pass, then re-stream.
        # Simpler approach: use send (non-streaming) for correctness, then wrap.
        # For true streaming, use a synchronization point.
        #
        # Use the two-phase approach: open the stream, grab headers, return
        # StreamingResponse wrapping the already-open response.
        req = client.build_request(
            request.method,
            upstream_url,
            headers=upstream_headers,
            content=body,
        )
        # Remove any hop-by-hop headers that httpx may add automatically
        # (e.g. 'connection') before sending.
        for hop_header in list(req.headers.keys()):
            if hop_header.lower() in HOP_BY_HOP_HEADERS:
                del req.headers[hop_header]
        upstream_resp = await client.send(req, stream=True)

        # Strip hop-by-hop headers from the upstream response.
        response_headers: dict[str, str] = {
            k: v
            for k, v in upstream_resp.headers.items()
            if k.lower() not in HOP_BY_HOP_HEADERS
        }

        async def _body_iter() -> AsyncIterator[bytes]:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
            await upstream_resp.aclose()

        return StreamingResponse(
            _body_iter(),
            status_code=upstream_resp.status_code,
            headers=response_headers,
        )

    except (httpx.TimeoutException, httpx.TransportError, httpx.NetworkError) as exc:
        return Response(
            content=b"upstream unavailable",
            status_code=502,
            headers={"X-RAC-Upstream-Error": type(exc).__name__},
        )
