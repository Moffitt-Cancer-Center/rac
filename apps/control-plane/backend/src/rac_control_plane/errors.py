# pattern: Functional Core
"""Typed API error classes and error rendering.

All errors carry code, http_status, and public_message.
render_error() produces the safe response dict (no stack traces, no internal URIs).

Design note on frozen-dataclass avoidance
------------------------------------------
Using @dataclass(frozen=True) on Exception subclasses causes
``FrozenInstanceError: cannot assign to field '__traceback__'`` when
Starlette's AsyncExitStack re-raises exceptions through BaseHTTPMiddleware.
Python's exception machinery needs to set __traceback__, __context__,
__cause__, and __suppress_context__ on exception instances.

We implement custom __setattr__ that:
- Permits the four exception-plumbing dunder attributes.
- Raises AttributeError for all other attribute mutations, preserving
  effective immutability for all application fields.
"""

# Attributes Python's exception machinery must be allowed to set.
_EXC_DUNDERS = frozenset(
    {"__traceback__", "__context__", "__cause__", "__suppress_context__"}
)


class ApiError(Exception):
    """Base API error with code, HTTP status, and safe public message."""

    __slots__ = ("code", "http_status", "public_message")

    # Explicit annotations so mypy resolves attribute access
    code: str
    http_status: int
    public_message: str

    def __init__(self, code: str, http_status: int, public_message: str) -> None:
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "http_status", http_status)
        object.__setattr__(self, "public_message", public_message)
        super().__init__(public_message)

    def __setattr__(self, name: str, value: object) -> None:
        if name in _EXC_DUNDERS:
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(f"cannot assign to field {name!r}")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"code={self.code!r}, "
            f"http_status={self.http_status!r}, "
            f"public_message={self.public_message!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ApiError):
            return NotImplemented
        return (
            type(self) is type(other)
            and self.code == other.code
            and self.http_status == other.http_status
            and self.public_message == other.public_message
        )

    def __hash__(self) -> int:
        return hash((type(self), self.code, self.http_status, self.public_message))


class NotFoundError(ApiError):
    """404: Resource not found."""

    __slots__ = ()

    def __init__(self, public_message: str) -> None:
        super().__init__(code="not_found", http_status=404, public_message=public_message)


class ValidationApiError(ApiError):
    """422: Validation failed. Optional per-field details for the frontend."""

    __slots__ = ("details",)

    details: list[dict[str, str]]

    def __init__(
        self,
        code: str,
        public_message: str,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(code=code, http_status=422, public_message=public_message)
        object.__setattr__(self, "details", list(details) if details else [])


class AuthError(ApiError):
    """401: Authentication required."""

    __slots__ = ()

    def __init__(self, public_message: str) -> None:
        super().__init__(code="unauthorized", http_status=401, public_message=public_message)


class ForbiddenError(ApiError):
    """403: Forbidden."""

    __slots__ = ()

    def __init__(self, public_message: str) -> None:
        super().__init__(code="forbidden", http_status=403, public_message=public_message)


class ConflictError(ApiError):
    """409: Conflict."""

    __slots__ = ()

    def __init__(self, public_message: str) -> None:
        super().__init__(code="conflict", http_status=409, public_message=public_message)


def render_error(exc: ApiError, correlation_id: str) -> dict[str, object]:
    """Render API error to a safe response dict with correlation ID.

    Returns {code, message, correlation_id}.  When the exception is a
    ValidationApiError with non-empty details, also includes ``details``.
    Never includes stack traces, Postgres error text, or internal URIs.
    """
    body: dict[str, object] = {
        "code": exc.code,
        "message": exc.public_message,
        "correlation_id": correlation_id,
    }
    if isinstance(exc, ValidationApiError) and exc.details:
        body["details"] = list(exc.details)
    return body
