# pattern: Imperative Shell
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from rac_control_plane.correlation import CorrelationIdMiddleware, get_correlation_id
from rac_control_plane.errors import ApiError, render_error
from rac_control_plane.logging_setup import configure_logging
from rac_control_plane.metrics import configure_metrics
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore
    """Application lifespan: startup and shutdown."""
    settings = get_settings()

    # Startup
    configure_logging(settings)
    configure_metrics(settings.otlp_endpoint)
    logger.info("RAC Control Plane starting", version="1.0.0", env=settings.env)

    yield

    # Shutdown
    logger.info("RAC Control Plane shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="RAC Control Plane",
        version="1.0.0",
        description="Research Application Commons: submission intake and approval",
        lifespan=lifespan,
    )

    # Middleware stack (order matters)
    app.add_middleware(CorrelationIdMiddleware)

    # TODO: Add IdempotencyHeaderMiddleware (Task 9)
    # TODO: Add auth middleware (Tasks 5-6)

    # Exception handlers
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        """Handle ApiError exceptions."""
        correlation_id = get_correlation_id()
        return JSONResponse(
            status_code=exc.http_status,
            content=render_error(exc, correlation_id),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Handle Starlette HTTPException."""
        correlation_id = get_correlation_id()

        # Convert to ApiError for consistent rendering
        api_error = ApiError(
            code=f"http_{exc.status_code}",
            http_status=exc.status_code,
            public_message=exc.detail or "An error occurred",
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
        settings = get_settings()
        return {
            "status": "healthy",
            "version": "1.0.0",
            "env": settings.env,
        }

    # Test-only endpoint for verifying error handling
    @app.get("/debug/error")
    async def debug_error() -> None:  # type: ignore
        """Test-only endpoint that raises an unhandled error."""
        raise RuntimeError("test error")

    # Test-only endpoint for verifying auth
    @app.get("/me")
    async def get_current_principal() -> dict:
        """Test-only endpoint that returns current principal from auth.

        Protected by auth middleware; will be 401 if no valid token.
        """
        # This endpoint will be protected by auth middleware in Task 5-6
        # For now, return a placeholder
        return {"status": "auth endpoint available"}

    # Static file mount for React SPA (must be last)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="root")

    return app


# Create the default app instance for uvicorn
app = create_app()
