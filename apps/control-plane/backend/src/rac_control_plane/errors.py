# pattern: Functional Core
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApiError(Exception):
    """Base API error with code, HTTP status, and public message."""

    code: str
    http_status: int
    public_message: str


@dataclass(frozen=True)
class NotFoundError(ApiError):
    """404: Resource not found."""

    def __init__(self, public_message: str) -> None:
        object.__setattr__(self, "code", "not_found")
        object.__setattr__(self, "http_status", 404)
        object.__setattr__(self, "public_message", public_message)


@dataclass(frozen=True)
class ValidationApiError(ApiError):
    """422: Validation failed. Optional per-field details for the frontend."""

    details: list[dict[str, str]] = field(default_factory=list)

    def __init__(
        self,
        code: str,
        public_message: str,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "http_status", 422)
        object.__setattr__(self, "public_message", public_message)
        object.__setattr__(self, "details", list(details) if details else [])


@dataclass(frozen=True)
class AuthError(ApiError):
    """401: Authentication required."""

    def __init__(self, public_message: str) -> None:
        object.__setattr__(self, "code", "unauthorized")
        object.__setattr__(self, "http_status", 401)
        object.__setattr__(self, "public_message", public_message)


@dataclass(frozen=True)
class ForbiddenError(ApiError):
    """403: Forbidden."""

    def __init__(self, public_message: str) -> None:
        object.__setattr__(self, "code", "forbidden")
        object.__setattr__(self, "http_status", 403)
        object.__setattr__(self, "public_message", public_message)


@dataclass(frozen=True)
class ConflictError(ApiError):
    """409: Conflict."""

    def __init__(self, public_message: str) -> None:
        object.__setattr__(self, "code", "conflict")
        object.__setattr__(self, "http_status", 409)
        object.__setattr__(self, "public_message", public_message)


def render_error(exc: ApiError, correlation_id: str) -> dict[str, object]:
    """Render API error to response dict with correlation ID.

    Returns {code, message, correlation_id} plus, when the exception is a
    ValidationApiError with non-empty details, a `details: [...]` field.
    Never includes stack traces, Postgres text, or internal URIs.
    """
    body: dict[str, object] = {
        "code": exc.code,
        "message": exc.public_message,
        "correlation_id": correlation_id,
    }
    if isinstance(exc, ValidationApiError) and exc.details:
        body["details"] = list(exc.details)
    return body
