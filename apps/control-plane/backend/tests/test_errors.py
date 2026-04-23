"""Tests for error handling module."""
import uuid

from hypothesis import given
from hypothesis import strategies as st

from rac_control_plane.errors import (
    ApiError,
    AuthError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationApiError,
    render_error,
)


def test_not_found_error() -> None:
    """NotFoundError has correct code and status."""
    error = NotFoundError("resource not found")
    assert error.code == "not_found"
    assert error.http_status == 404
    assert error.public_message == "resource not found"


def test_validation_api_error() -> None:
    """ValidationApiError accepts custom code."""
    error = ValidationApiError("email_invalid", "email format is invalid")
    assert error.code == "email_invalid"
    assert error.http_status == 422
    assert error.public_message == "email format is invalid"


def test_auth_error() -> None:
    """AuthError has correct code and status."""
    error = AuthError("authentication required")
    assert error.code == "unauthorized"
    assert error.http_status == 401
    assert error.public_message == "authentication required"


def test_forbidden_error() -> None:
    """ForbiddenError has correct code and status."""
    error = ForbiddenError("you do not have permission")
    assert error.code == "forbidden"
    assert error.http_status == 403
    assert error.public_message == "you do not have permission"


def test_conflict_error() -> None:
    """ConflictError has correct code and status."""
    error = ConflictError("resource already exists")
    assert error.code == "conflict"
    assert error.http_status == 409
    assert error.public_message == "resource already exists"


def test_render_error_shape() -> None:
    """render_error returns exactly {code, message, correlation_id}."""
    error = NotFoundError("not found")
    correlation_id = str(uuid.uuid4())

    result = render_error(error, correlation_id)

    assert set(result.keys()) == {"code", "message", "correlation_id"}
    assert result["code"] == "not_found"
    assert result["message"] == "not found"
    assert result["correlation_id"] == correlation_id


def test_render_error_no_internal_details() -> None:
    """render_error never includes str(exc) or internal details."""
    error = ValidationApiError("github_not_found", "repository not found at URL")
    correlation_id = str(uuid.uuid4())

    result = render_error(error, correlation_id)

    # Should not contain the exception's string representation
    result_str = str(result)
    assert "ValidationApiError" not in result_str
    assert "Traceback" not in result_str
    assert "github_not_found" in result_str  # code is OK
    assert "repository not found at URL" in result_str  # message is OK


@given(
    code=st.text(min_size=1),
    message=st.text(),
    correlation_id=st.uuids().map(str),
)
def test_render_error_has_required_keys(
    code: str, message: str, correlation_id: str
) -> None:
    """Property test: render_error always has exactly {code, message, correlation_id}."""
    error = ValidationApiError(code, message)
    result = render_error(error, correlation_id)

    # Output must have exactly these keys
    assert set(result.keys()) == {"code", "message", "correlation_id"}

    # Values must be strings
    assert isinstance(result["code"], str)
    assert isinstance(result["message"], str)
    assert isinstance(result["correlation_id"], str)

    # No empty dicts or None values
    assert result["code"] == code
    assert result["message"] == message
    assert result["correlation_id"] == correlation_id


@given(st.uuids().map(str))
def test_render_error_correlation_id_always_present(correlation_id: str) -> None:
    """Property test: correlation_id is always in output."""
    error = AuthError("unauthorized")
    result = render_error(error, correlation_id)

    assert "correlation_id" in result
    assert result["correlation_id"] == correlation_id
