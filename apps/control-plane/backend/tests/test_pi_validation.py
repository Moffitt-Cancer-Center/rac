"""Tests for PI validation — Functional Core.

Verifies:
- AC9.1: is_valid_pi returns Ok for active users.
- AC9.1: is_valid_pi returns Invalid(account_disabled) for disabled users.
- AC9.1: is_valid_pi returns Invalid(not_found) when user is None.
- Property test: is_valid_pi is pure (same input → same output).
"""

from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rac_control_plane.services.ownership.graph_gateway import GraphUser
from rac_control_plane.services.ownership.pi_validation import (
    Invalid,
    Ok,
    ValidationResult,
    is_valid_pi,
)


def _active_user(oid: UUID | None = None) -> GraphUser:
    """Build a GraphUser that represents a valid, active Entra user."""
    return GraphUser(
        oid=oid or uuid4(),
        account_enabled=True,
        display_name="Test PI",
        user_principal_name="pi@example.com",
        department="Bioinformatics",
    )


def _disabled_user(oid: UUID | None = None) -> GraphUser:
    """Build a GraphUser whose account has been disabled."""
    return GraphUser(
        oid=oid or uuid4(),
        account_enabled=False,
        display_name="Former PI",
        user_principal_name="former@example.com",
        department=None,
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_active_user_returns_ok() -> None:
    """An active Entra user is a valid PI."""
    result = is_valid_pi(_active_user())
    assert isinstance(result, Ok)


def test_disabled_user_returns_invalid_account_disabled() -> None:
    """A disabled Entra account is invalid with reason 'account_disabled'."""
    result = is_valid_pi(_disabled_user())
    assert isinstance(result, Invalid)
    assert result.reason == "account_disabled"


def test_none_user_returns_invalid_not_found() -> None:
    """A None response from Graph means user not found → Invalid(not_found)."""
    result = is_valid_pi(None)
    assert isinstance(result, Invalid)
    assert result.reason == "not_found"


def test_active_user_optional_fields_none() -> None:
    """Active user with no display_name/department is still valid."""
    user = GraphUser(
        oid=uuid4(),
        account_enabled=True,
        display_name=None,
        user_principal_name=None,
        department=None,
    )
    result = is_valid_pi(user)
    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------
# Property tests — purity
# ---------------------------------------------------------------------------

@given(
    account_enabled=st.booleans(),
    display_name=st.one_of(st.none(), st.text(max_size=100)),
    upn=st.one_of(st.none(), st.text(max_size=100)),
    dept=st.one_of(st.none(), st.text(max_size=100)),
)
def test_is_valid_pi_is_pure(
    account_enabled: bool,
    display_name: str | None,
    upn: str | None,
    dept: str | None,
) -> None:
    """Property: is_valid_pi is pure — same input always produces same output."""
    oid = uuid4()
    user = GraphUser(
        oid=oid,
        account_enabled=account_enabled,
        display_name=display_name,
        user_principal_name=upn,
        department=dept,
    )
    result1 = is_valid_pi(user)
    result2 = is_valid_pi(user)
    assert result1 == result2


@given(
    account_enabled=st.booleans(),
)
def test_is_valid_pi_none_always_not_found(account_enabled: bool) -> None:
    """Property: None user always produces Invalid(not_found), regardless of other state."""
    result = is_valid_pi(None)
    assert isinstance(result, Invalid)
    assert result.reason == "not_found"


@given(st.booleans())
def test_is_valid_pi_disabled_always_account_disabled(dummy: bool) -> None:
    """Property: a disabled user always produces Invalid(account_disabled)."""
    user = GraphUser(
        oid=uuid4(),
        account_enabled=False,
        display_name=None,
        user_principal_name=None,
        department=None,
    )
    result = is_valid_pi(user)
    assert isinstance(result, Invalid)
    assert result.reason == "account_disabled"
