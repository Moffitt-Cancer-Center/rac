# pattern: Imperative Shell
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from rac_control_plane.api.routes.agents import router as agents_router
from rac_control_plane.api.routes.approvals import router as approvals_router
from rac_control_plane.api.routes.cost import router as cost_router
from rac_control_plane.api.routes.findings import router as findings_router
from rac_control_plane.api.routes.jobs import router as jobs_router
from rac_control_plane.api.routes.ownership import router as ownership_router
from rac_control_plane.api.routes.provisioning import router as provisioning_router
from rac_control_plane.api.routes.submissions import router as submissions_router
from rac_control_plane.api.routes.webhook_subscriptions import router as webhook_subs_router
from rac_control_plane.api.routes.webhooks import router as webhooks_router
from rac_control_plane.correlation import CorrelationIdMiddleware, get_correlation_id
from rac_control_plane.data.db import get_session_maker
from rac_control_plane.errors import ApiError, render_error
from rac_control_plane.logging_setup import configure_logging
from rac_control_plane.metrics import configure_metrics
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Application lifespan: startup and shutdown."""
    settings = get_settings()

    # Startup
    configure_logging(settings)

    if settings.metrics_enabled:
        try:
            configure_metrics(settings.otlp_endpoint)
        except Exception as exc:
            logger.warning(
                "OTLP metrics export unavailable, skipping",
                endpoint=settings.otlp_endpoint,
                error=str(exc),
            )

    # Pre-load detection rules so routes don't pay discovery cost per request.
    try:
        from rac_control_plane.detection.discovery import load_rules
        app.state.rules = load_rules()
        logger.info("detection_rules_loaded", rule_count=len(app.state.rules))
    except Exception as exc:
        # Non-fatal: routes fall back to lazy load_rules() on each request.
        logger.warning("detection_rules_load_failed", error=str(exc))
        app.state.rules = None

    logger.info("RAC Control Plane starting", version="1.0.0", env=settings.env)

    yield

    # Shutdown
    logger.info("RAC Control Plane shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="RAC Control Plane",
        version="1.0.0",
        description="Research Application Commons: submission intake and approval",
        lifespan=lifespan,
    )

    # Middleware stack (order matters: outermost first, i.e., first added runs last)
    app.add_middleware(CorrelationIdMiddleware)

    # Idempotency middleware needs its own session factory (not request-scoped DI)
    from rac_control_plane.api.middleware.idempotency import IdempotencyMiddleware
    app.add_middleware(IdempotencyMiddleware, session_factory=get_session_maker())

    # Exception handlers
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        """Handle ApiError exceptions."""
        correlation_id = get_correlation_id()
        headers: dict[str, str] = {}
        if exc.http_status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.http_status,
            content=render_error(exc, correlation_id),
            headers=headers,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Handle Starlette HTTPException."""
        correlation_id = get_correlation_id()

        # Convert to ApiError for consistent rendering
        api_error = ApiError(
            code=f"http_{exc.status_code}",
            http_status=exc.status_code,
            public_message=str(exc.detail) if exc.detail else "An error occurred",
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=render_error(api_error, correlation_id),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all handler: log traceback, return generic 500."""
        correlation_id = get_correlation_id()

        # Log with full traceback
        logger.exception("unhandled exception", exc_info=exc, correlation_id=correlation_id)

        # Return generic error without internal details
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "an unexpected error occurred",
                "correlation_id": correlation_id,
            },
        )

    # Health check endpoint
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        _settings = get_settings()
        return {
            "status": "healthy",
            "version": "1.0.0",
            "env": _settings.env,
        }

    # Test-only /me endpoint — returns current principal info
    @app.get("/me")
    async def get_current_principal(request: Request) -> dict[str, object]:
        """Test endpoint that returns current principal from auth dependency."""
        from rac_control_plane.auth.dependencies import current_principal
        from rac_control_plane.data.db import get_session

        # Manually invoke the dependency
        async for session in get_session():
            principal = await current_principal(request, session)
            return {
                "oid": str(principal.oid),
                "kind": principal.kind,
                "display_name": principal.display_name,
                "agent_id": str(principal.agent_id) if principal.agent_id else None,
                "roles": list(principal.roles),
            }
        return {}  # unreachable, but makes mypy happy

    # Debug/error endpoint — only in dev environment
    if settings.env == "dev":
        @app.get("/debug/error")
        async def debug_error() -> None:
            """Test-only endpoint that raises an unhandled error."""
            raise RuntimeError("test error")

    # Register routers
    app.include_router(submissions_router, prefix="")
    app.include_router(approvals_router, prefix="")
    app.include_router(findings_router, prefix="")
    app.include_router(agents_router, prefix="")
    app.include_router(webhooks_router, prefix="")
    app.include_router(webhook_subs_router, prefix="")
    app.include_router(jobs_router, prefix="")
    app.include_router(provisioning_router, prefix="")
    app.include_router(ownership_router, prefix="")
    app.include_router(cost_router, prefix="")

    # Static file mount for React SPA (must be last)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="root")

    return app


def _make_app() -> FastAPI:
    """Create the app, gracefully skipping if settings are unavailable.

    This ensures that importing the module in tests (without env vars) does not fail.
    """
    try:
        return create_app()
    except Exception:
        # During test collection, environment may not be configured yet.
        # Tests call create_app() explicitly via the fixture.
        return FastAPI(title="RAC Control Plane (unconfigured)")


# Create the default app instance for uvicorn / gunicorn
app = _make_app()
