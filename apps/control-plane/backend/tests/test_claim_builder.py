"""Tests for services/tokens/claim_builder.py — pure reviewer claim builder."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rac_control_plane.services.tokens.claim_builder import build_reviewer_claims

# ---------------------------------------------------------------------------
# Concrete tests
# ---------------------------------------------------------------------------

def _make_claims(**overrides: object) -> dict[str, object]:
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    exp = now + timedelta(days=30)
    defaults: dict[str, object] = dict(
        app_slug="myapp",
        reviewer_label="Reviewer #1",
        issuer="https://rac.example.com",
        issued_at=now,
        expires_at=exp,
        jti=uuid4(),
    )
    defaults.update(overrides)
    return build_reviewer_claims(**defaults)  # type: ignore[arg-type]


def test_claims_has_seven_fields() -> None:
    claims = _make_claims()
    assert len(claims) == 7


def test_iss_matches_issuer() -> None:
    claims = _make_claims(issuer="https://cp.example.com")
    assert claims["iss"] == "https://cp.example.com"


def test_aud_is_rac_app_prefixed() -> None:
    claims = _make_claims(app_slug="myapp")
    assert claims["aud"] == "rac-app:myapp"


def test_sub_is_reviewer_label() -> None:
    claims = _make_claims(reviewer_label="Journal Reviewer #3")
    assert claims["sub"] == "Journal Reviewer #3"


def test_jti_is_string_uuid() -> None:
    jti = uuid4()
    claims = _make_claims(jti=jti)
    assert isinstance(claims["jti"], str)
    # Must round-trip as a valid UUID
    assert UUID(str(claims["jti"])) == jti


def test_exp_gt_iat() -> None:
    claims = _make_claims()
    assert int(str(claims["exp"])) > int(str(claims["iat"]))


def test_scope_defaults_to_read() -> None:
    claims = _make_claims()
    assert claims["scope"] == "read"


def test_scope_custom() -> None:
    claims = _make_claims(scope="write")
    assert claims["scope"] == "write"


def test_iat_is_unix_timestamp() -> None:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    claims = _make_claims(issued_at=now, expires_at=now + timedelta(days=1))
    assert claims["iat"] == int(now.timestamp())


# ---------------------------------------------------------------------------
# Property tests with Hypothesis
# ---------------------------------------------------------------------------

_text = st.text(min_size=1, max_size=100, alphabet=st.characters(blacklist_categories=("Cs",)))
_slugs = st.text(min_size=1, max_size=40, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-")
_uuids = st.uuids()

_base_dt = datetime(2025, 1, 1, tzinfo=UTC)
_datetimes = st.datetimes(
    min_value=datetime(2025, 1, 1),
    max_value=datetime(2035, 1, 1),
    timezones=st.just(UTC),
)


@given(
    app_slug=_slugs,
    reviewer_label=_text,
    issuer=_text,
    jti=_uuids,
    ttl_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=50)
def test_property_always_seven_fields(
    app_slug: str,
    reviewer_label: str,
    issuer: str,
    jti: UUID,
    ttl_days: int,
) -> None:
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    exp = now + timedelta(days=ttl_days)
    claims = build_reviewer_claims(
        app_slug=app_slug,
        reviewer_label=reviewer_label,
        issuer=issuer,
        issued_at=now,
        expires_at=exp,
        jti=jti,
    )
    assert len(claims) == 7


@given(
    jti=_uuids,
    ttl_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=50)
def test_property_exp_gt_iat(jti: UUID, ttl_days: int) -> None:
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    exp = now + timedelta(days=ttl_days)
    claims = build_reviewer_claims(
        app_slug="slug",
        reviewer_label="label",
        issuer="https://iss",
        issued_at=now,
        expires_at=exp,
        jti=jti,
    )
    assert int(str(claims["exp"])) > int(str(claims["iat"]))


@given(jti=_uuids)
@settings(max_examples=50)
def test_property_jti_valid_uuid(jti: UUID) -> None:
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    exp = now + timedelta(days=30)
    claims = build_reviewer_claims(
        app_slug="slug",
        reviewer_label="label",
        issuer="https://iss",
        issued_at=now,
        expires_at=exp,
        jti=jti,
    )
    # jti is a valid UUID string
    assert UUID(str(claims["jti"])) == jti
