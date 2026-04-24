# pattern: Imperative Shell
"""Starlette main application — the single entry point for all researcher app traffic.

Flow:
1. Lifespan: open PG pool, init caches + registry + batch writer.
2. Catch-all route handles every request:
   a. Resolve app slug from Host header via AppRegistry.
   b. Public mode: proxy directly, log, return.
   c. Token-required mode: validate query token or cookie; set cookie on
      first-use; render error pages on failure; proxy on success.
3. Cold-start detection: 502 from upstream → serve interstitial + wake task.
4. Every branch writes an AccessRecord to the batch writer.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg
import httpx
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from rac_shim.app_registry import AppRegistry
from rac_shim.audit.access_record import RequestInfo, build_record
from rac_shim.audit.batch_writer import AccessLogBatchWriter
from rac_shim.cold_start.decision import decide as cold_start_decide
from rac_shim.metrics import (
    configure_metrics,
    token_validation_counter,
    wake_up_duration_histogram,
)
from rac_shim.proxy.forward import proxy_request
from rac_shim.proxy.wake_up import wake as wake_upstream
from rac_shim.routing.decision import AppRoute, route_for_host
from rac_shim.token.cookie import build_cookie_header, extract_session_jti
from rac_shim.token.denylist_cache import RevokedTokenDenylistCache
from rac_shim.token.errors import Expired, Revoked, TokenInvalid
from rac_shim.token.kv_key_cache import KeyVaultPublicKeyCache
from rac_shim.token.validation import decode_unverified_header, verify_signature_and_claims
from rac_shim.ui.render import ErrorContext, InterstitialContext, render_error, render_interstitial

log: structlog.BoundLogger = structlog.get_logger(__name__)


async def _wake_and_record(upstream_host: str, *, client: httpx.AsyncClient) -> None:
    """Wake the upstream and record the wall-clock duration to the histogram.
    Intended to be spawned via asyncio.create_task from the cold-start path."""
    elapsed_ms = await wake_upstream(upstream_host, client=client)
    if elapsed_ms is not None:
        wake_up_duration_histogram.record(elapsed_ms)


# ---------------------------------------------------------------------------
# Helper: build a clean URL without the rac_token query parameter
# ---------------------------------------------------------------------------


def _strip_rac_token(url: str) -> str:
    """Return the URL with rac_token removed from the query string."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("rac_token", None)
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Helper: extract source IP
# ---------------------------------------------------------------------------


def _source_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Dependency container (injected at startup, testable)
# ---------------------------------------------------------------------------


class _Deps:
    """Mutable container for all shared state; injected via app.state."""

    pg_pool: asyncpg.Pool  # asyncpg generic Pool
    kv_key_cache: KeyVaultPublicKeyCache
    denylist_cache: RevokedTokenDenylistCache
    batch_writer: AccessLogBatchWriter
    app_registry: AppRegistry
    httpx_client: httpx.AsyncClient
    settings: Any  # ShimSettings — imported lazily to allow testing without env vars


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def _health(request: Request) -> Response:
    return Response(content=b"ok", status_code=200, media_type="text/plain")


async def _wake_endpoint(request: Request) -> Response:
    """Internal wake endpoint polled by the interstitial JS."""
    return Response(content=b"", status_code=204)


async def _handle(request: Request) -> Response:  # noqa: PLR0911, PLR0912
    """Main catch-all handler."""
    deps: _Deps = request.app.state.deps
    settings = deps.settings

    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    host = request.headers.get("host", "")
    route: AppRoute | None = route_for_host(
        host,
        parent_domain=settings.parent_domain,
        routes=deps.app_registry.all(),
    )

    if route is None:
        log.warning("unknown_host", host=host)
        return Response(status_code=404, content=b"Not Found")

    log.info(
        "request_received",
        slug=route.slug,
        method=request.method,
        path=request.url.path,
        access_mode=route.access_mode,
    )

    request_info = RequestInfo(
        host=host,
        path=request.url.path,
        method=request.method,
        user_agent=request.headers.get("user-agent"),
        source_ip=_source_ip(request),
        request_id=uuid.UUID(correlation_id),
    )

    hmac_secret = settings.cookie_hmac_secret.get_secret_value().encode()
    now = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Public mode — no token check
    # ------------------------------------------------------------------
    if route.access_mode == "public":
        proxy_start = time.monotonic()
        upstream_resp = await proxy_request(
            request,
            upstream_host=route.upstream_host,
            reviewer_label=None,
            reviewer_jti=None,
            app_slug=route.slug,
            client=deps.httpx_client,
        )
        latency_ms = int((time.monotonic() - proxy_start) * 1000)

        cold = cold_start_decide(
            upstream_resp.status_code if upstream_resp.status_code != 502 else None,
            None,
            cold_start_threshold_ms=settings.cold_start_threshold_ms,
        )
        if cold.should_serve_interstitial:
            asyncio.create_task(
                _wake_and_record(route.upstream_host, client=deps.httpx_client),
                name=f"wake_{route.slug}",
            )
            ictx = InterstitialContext(
                institution_name=settings.institution_name,
                brand_logo_url=settings.brand_logo_url,
                access_mode="public",
                correlation_id=correlation_id,
            )
            _append_record(
                deps,
                request_info=request_info,
                route=route,
                token_jti=None,
                upstream_status=None,
                latency_ms=latency_ms,
                now=now,
            )
            return HTMLResponse(
                render_interstitial(ictx).decode(),
                status_code=200,
                headers={"X-Correlation-Id": correlation_id},
            )

        _append_record(
            deps,
            request_info=request_info,
            route=route,
            token_jti=None,
            upstream_status=upstream_resp.status_code,
            latency_ms=latency_ms,
            now=now,
        )
        upstream_resp.headers["X-Correlation-Id"] = correlation_id
        return upstream_resp

    # ------------------------------------------------------------------
    # Token-required mode
    # ------------------------------------------------------------------
    query_token = request.query_params.get("rac_token")
    cookie_value = request.cookies.get("rac_session")
    cookie_jti = extract_session_jti(cookie_value, hmac_secret=hmac_secret, now=now)
    token_jti: uuid.UUID | None = None

    if query_token:
        # First-use path: validate token, set cookie, redirect to clean URL.
        try:
            _header = decode_unverified_header(query_token)
            key_name = f"rac-app-{route.slug}-v1"
            public_key = await deps.kv_key_cache.get_jwk(key_name)
            claims = verify_signature_and_claims(
                query_token,
                public_key=public_key,
                expected_issuer=settings.issuer,
                expected_audience=f"rac-app:{route.slug}",
                now=now,
            )
            if await deps.denylist_cache.check(claims.jti):
                raise Revoked("token is in revoked_token table")

            token_validation_counter.add(1, {"result": "valid"})
            # Success: set cookie, redirect to clean URL
            cookie_header = build_cookie_header(
                claims,
                hmac_secret=hmac_secret,
                issued_at=now,
                max_age_seconds=settings.cookie_max_age_seconds,
                cookie_domain=settings.cookie_domain,
            )
            clean_url = _strip_rac_token(str(request.url))
            log.info(
                "token_valid_redirecting",
                slug=route.slug,
                jti=str(claims.jti),
            )
            _append_record(
                deps,
                request_info=request_info,
                route=route,
                token_jti=claims.jti,
                upstream_status=None,
                latency_ms=0,
                now=now,
            )
            return RedirectResponse(
                url=clean_url,
                status_code=302,
                headers={
                    "Set-Cookie": cookie_header,
                    "X-Correlation-Id": correlation_id,
                },
            )

        except TokenInvalid as exc:
            # Emit metric labeled by validation outcome (AC10.2).
            if isinstance(exc, Expired):
                result_label = "expired"
            elif isinstance(exc, Revoked):
                result_label = "revoked"
            else:
                result_label = "malformed"
            token_validation_counter.add(1, {"result": result_label})
            log.warning(
                "token_invalid",
                slug=route.slug,
                code=exc.code,
            )
            _append_record(
                deps,
                request_info=request_info,
                route=route,
                token_jti=None,
                upstream_status=None,
                latency_ms=0,
                now=now,
            )
            return _error_response(exc, settings, correlation_id)

    elif cookie_jti:
        # Subsequent requests via cookie.
        if await deps.denylist_cache.check(cookie_jti):
            revoked_exc = Revoked("jti in denylist")
            _append_record(
                deps,
                request_info=request_info,
                route=route,
                token_jti=None,
                upstream_status=None,
                latency_ms=0,
                now=now,
            )
            return _error_response(revoked_exc, settings, correlation_id)
        # Valid cookie — proceed to proxy
        token_jti = cookie_jti

    else:
        # No token, no valid cookie.

        _append_record(
            deps,
            request_info=request_info,
            route=route,
            token_jti=None,
            upstream_status=None,
            latency_ms=0,
            now=now,
        )
        ectx = ErrorContext(
            institution_name=settings.institution_name,
            brand_logo_url=settings.brand_logo_url,
            researcher_contact_email=None,
            pi_name=None,
            correlation_id=correlation_id,
        )
        return HTMLResponse(
            render_error("no_token", ectx).decode(),
            status_code=403,
            headers={"X-Correlation-Id": correlation_id},
        )

    # ------------------------------------------------------------------
    # Proxy (token_required, valid token/cookie)
    # ------------------------------------------------------------------
    proxy_start = time.monotonic()
    upstream_resp = await proxy_request(
        request,
        upstream_host=route.upstream_host,
        reviewer_label=str(token_jti) if token_jti else None,
        reviewer_jti=str(token_jti) if token_jti else None,
        app_slug=route.slug,
        client=deps.httpx_client,
    )
    latency_ms = int((time.monotonic() - proxy_start) * 1000)

    cold = cold_start_decide(
        upstream_resp.status_code if upstream_resp.status_code != 502 else None,
        float(latency_ms),
        cold_start_threshold_ms=settings.cold_start_threshold_ms,
    )

    if cold.should_serve_interstitial:
        asyncio.create_task(
            _wake_and_record(route.upstream_host, client=deps.httpx_client),
            name=f"wake_{route.slug}",
        )
        ictx = InterstitialContext(
            institution_name=settings.institution_name,
            brand_logo_url=settings.brand_logo_url,
            access_mode="token_required",
            correlation_id=correlation_id,
        )
        _append_record(
            deps,
            request_info=request_info,
            route=route,
            token_jti=token_jti,
            upstream_status=None,
            latency_ms=latency_ms,
            now=now,
        )
        return HTMLResponse(
            render_interstitial(ictx).decode(),
            status_code=200,
            headers={"X-Correlation-Id": correlation_id},
        )

    _append_record(
        deps,
        request_info=request_info,
        route=route,
        token_jti=token_jti,
        upstream_status=upstream_resp.status_code,
        latency_ms=latency_ms,
        now=now,
    )
    upstream_resp.headers["X-Correlation-Id"] = correlation_id
    return upstream_resp


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _error_response(exc: TokenInvalid, settings: Any, correlation_id: str) -> HTMLResponse:
    """Map a TokenInvalid exception to an HTML error response (AC7.4)."""
    ectx = ErrorContext(
        institution_name=settings.institution_name,
        brand_logo_url=settings.brand_logo_url,
        researcher_contact_email=None,
        pi_name=None,
        correlation_id=correlation_id,
    )
    code = exc.code
    if code == "expired":
        error_code: str = "expired"
    elif code == "revoked":
        error_code = "revoked"
    else:
        # All other codes map to generic page (AC7.4)
        error_code = "generic"


    rendered = render_error(error_code, ectx)  # type: ignore[arg-type]
    return HTMLResponse(
        rendered.decode(),
        status_code=403,
        headers={"X-Correlation-Id": correlation_id},
    )


def _append_record(
    deps: _Deps,
    *,
    request_info: RequestInfo,
    route: AppRoute,
    token_jti: uuid.UUID | None,
    upstream_status: int | None,
    latency_ms: int,
    now: datetime,
) -> None:
    record = build_record(
        request_info=request_info,
        app_id=route.app_id,
        submission_id=None,
        access_mode=route.access_mode,
        token_jti=token_jti,
        upstream_status=upstream_status,
        latency_ms=latency_ms,
        created_at=now,
        record_id=uuid.uuid4(),
    )
    deps.batch_writer.append(record)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(deps: _Deps | None = None) -> Starlette:
    """Create the Starlette application.

    If ``deps`` is provided, it is used directly (for testing).
    In production, the lifespan context manager builds deps from settings.
    """
    _injected_deps = deps

    @asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        if _injected_deps is not None:
            app.state.deps = _injected_deps
            yield
            return

        from rac_shim.logging_setup import configure_logging  # noqa: PLC0415
        from rac_shim.settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        configure_logging()

        if settings.metrics_enabled:
            try:
                configure_metrics(settings.otlp_endpoint)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "otlp_export_unavailable_skipping",
                    endpoint=settings.otlp_endpoint,
                    error=str(exc),
                )

        pool = await asyncpg.create_pool(settings.database_dsn, min_size=2, max_size=10)
        if pool is None:
            raise RuntimeError("asyncpg.create_pool returned None")

        from azure.identity.aio import (
            ManagedIdentityCredential,  # noqa: PLC0415  # type: ignore[import-untyped]
        )

        credential = ManagedIdentityCredential()
        kv_cache = KeyVaultPublicKeyCache(settings.kv_uri, credential)
        denylist = RevokedTokenDenylistCache(pool)
        writer = AccessLogBatchWriter(
            pool,
            batch_size=settings.batch_writer_batch_size,
            flush_interval_seconds=settings.batch_writer_flush_interval_seconds,
            max_queue_size=settings.batch_writer_max_queue_size,
        )
        registry = AppRegistry(
            pool,
            aca_internal_suffix=settings.aca_internal_suffix,
            refresh_interval_seconds=settings.app_registry_refresh_interval_seconds,
        )
        client = httpx.AsyncClient(timeout=float(settings.wake_budget_seconds))

        await writer.start()
        await registry.start()

        d = _Deps()
        d.pg_pool = pool
        d.kv_key_cache = kv_cache
        d.denylist_cache = denylist
        d.batch_writer = writer
        d.app_registry = registry
        d.httpx_client = client
        d.settings = settings

        app.state.deps = d

        try:
            yield
        finally:
            await registry.stop()
            await writer.stop()
            await client.aclose()
            await pool.close()
            await credential.close()

    routes = [
        Route("/_shim/health", _health),
        Route("/_rac/wake", _wake_endpoint),
        Route(
            "/{path:path}",
            _handle,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
        Route(
            "/",
            _handle,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
    ]

    starlette_app = Starlette(routes=routes, lifespan=lifespan)
    # For test injection: set state directly so handlers can access deps
    # even without a lifespan context.
    if _injected_deps is not None:
        starlette_app.state.deps = _injected_deps
    return starlette_app


# Allow running with uvicorn directly.
app = create_app()
