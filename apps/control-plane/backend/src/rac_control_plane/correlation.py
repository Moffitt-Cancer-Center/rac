# pattern: Imperative Shell
import contextvars
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable for correlation ID
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that reads/generates X-Request-Id and binds to context."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Read X-Request-Id header or generate new UUID
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

        # Bind to structlog context
        correlation_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(correlation_id=request_id)

        # Process request
        response = await call_next(request)

        # Echo back in response header
        response.headers["X-Request-Id"] = request_id

        return response


def get_correlation_id() -> str:
    """Get current correlation ID from context."""
    return correlation_id_var.get()
