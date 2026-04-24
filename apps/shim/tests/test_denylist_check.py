"""Tests for rac_shim.token.denylist_check — pure set membership."""

import uuid

from rac_shim.token.denylist_check import is_revoked


def test_jti_in_denylist_returns_true() -> None:
    jti = uuid.uuid4()
    assert is_revoked(jti, frozenset([jti])) is True


def test_empty_denylist_returns_false() -> None:
    jti = uuid.uuid4()
    assert is_revoked(jti, frozenset()) is False


def test_jti_not_in_denylist_returns_false() -> None:
    jti = uuid.uuid4()
    other = uuid.uuid4()
    assert is_revoked(jti, frozenset([other])) is False


def test_denylist_with_multiple_entries() -> None:
    jti = uuid.uuid4()
    others = frozenset(uuid.uuid4() for _ in range(10))
    assert is_revoked(jti, others) is False
    denylist = others | frozenset([jti])
    assert is_revoked(jti, denylist) is True
