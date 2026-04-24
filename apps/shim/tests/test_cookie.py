"""Tests for rac_shim.token.cookie — pure HMAC-signed session cookie.

Verifies: rac-v1.AC7.1 (HttpOnly/Secure/SameSite=Lax cookie attributes).
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rac_shim.token.claims import RacTokenClaims
from rac_shim.token.cookie import build_cookie_header, build_cookie_value, extract_session_jti

SECRET = b"super-secret-hmac-key-for-testing"
OTHER_SECRET = b"different-secret"

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_claims(
    jti: uuid.UUID | None = None,
    exp_offset: int = 3600,
    aud: str = "rac-app:myapp",
) -> RacTokenClaims:
    j = jti or uuid.uuid4()
    return RacTokenClaims(
        iss="https://rac.example.com",
        aud=aud,
        sub="reviewer-1",
        jti=j,
        iat=NOW - timedelta(seconds=10),
        exp=NOW + timedelta(seconds=exp_offset),
    )


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_returns_same_jti() -> None:
    claims = _make_claims()
    value = build_cookie_value(claims, hmac_secret=SECRET, issued_at=NOW)
    result = extract_session_jti(value, hmac_secret=SECRET, now=NOW)
    assert result == claims.jti


def test_roundtrip_via_cookie_header() -> None:
    """extract_session_jti should handle 'rac_session=<value>' prefix."""
    claims = _make_claims()
    header = build_cookie_header(
        claims,
        hmac_secret=SECRET,
        issued_at=NOW,
        max_age_seconds=3600,
        cookie_domain=".rac.example.com",
    )
    # Extract just the cookie value portion from full Set-Cookie header
    match = re.search(r"rac_session=([^;]+)", header)
    assert match is not None
    cookie_value = "rac_session=" + match.group(1)
    result = extract_session_jti(cookie_value, hmac_secret=SECRET, now=NOW)
    assert result == claims.jti


# ---------------------------------------------------------------------------
# Security: tampered payload
# ---------------------------------------------------------------------------


def test_tampered_payload_returns_none() -> None:
    claims = _make_claims()
    value = build_cookie_value(claims, hmac_secret=SECRET, issued_at=NOW)
    # Tamper with the payload portion (first segment)
    parts = value.split(".")
    tampered = parts[0][:-4] + "XXXX"
    tampered_value = tampered + "." + parts[1]
    result = extract_session_jti(tampered_value, hmac_secret=SECRET, now=NOW)
    assert result is None


def test_wrong_hmac_secret_returns_none() -> None:
    claims = _make_claims()
    value = build_cookie_value(claims, hmac_secret=SECRET, issued_at=NOW)
    result = extract_session_jti(value, hmac_secret=OTHER_SECRET, now=NOW)
    assert result is None


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_cookie_returns_none() -> None:
    # exp is 1 second in the future from NOW, but we check at NOW + 2 seconds
    claims = _make_claims(exp_offset=1)
    value = build_cookie_value(claims, hmac_secret=SECRET, issued_at=NOW)
    future = NOW + timedelta(seconds=2)
    result = extract_session_jti(value, hmac_secret=SECRET, now=future)
    assert result is None


def test_not_expired_cookie_returns_jti() -> None:
    claims = _make_claims(exp_offset=3600)
    value = build_cookie_value(claims, hmac_secret=SECRET, issued_at=NOW)
    result = extract_session_jti(value, hmac_secret=SECRET, now=NOW)
    assert result == claims.jti


# ---------------------------------------------------------------------------
# Missing / empty
# ---------------------------------------------------------------------------


def test_none_header_returns_none() -> None:
    assert extract_session_jti(None, hmac_secret=SECRET, now=NOW) is None


def test_empty_string_returns_none() -> None:
    assert extract_session_jti("", hmac_secret=SECRET, now=NOW) is None


# ---------------------------------------------------------------------------
# Cookie attribute checks (AC7.1)
# ---------------------------------------------------------------------------


def test_cookie_header_has_required_attributes() -> None:
    claims = _make_claims()
    header = build_cookie_header(
        claims,
        hmac_secret=SECRET,
        issued_at=NOW,
        max_age_seconds=3600,
        cookie_domain=".rac.example.com",
    )
    assert re.search(r"\bHttpOnly\b", header) is not None
    assert re.search(r"\bSecure\b", header) is not None
    assert re.search(r"\bSameSite=Lax\b", header) is not None
    assert re.search(r"(?:^|;)\s*Path=/(?:;|$)", header) is not None


def test_cookie_header_max_age_matches_argument() -> None:
    claims = _make_claims()
    header = build_cookie_header(
        claims,
        hmac_secret=SECRET,
        issued_at=NOW,
        max_age_seconds=7200,
        cookie_domain=".rac.example.com",
    )
    assert re.search(r"\bMax-Age=7200\b", header) is not None


def test_cookie_header_domain_matches_argument() -> None:
    claims = _make_claims()
    header = build_cookie_header(
        claims,
        hmac_secret=SECRET,
        issued_at=NOW,
        max_age_seconds=3600,
        cookie_domain=".rac.example.com",
    )
    assert "Domain=.rac.example.com" in header


def test_cookie_value_has_two_segments() -> None:
    """Cookie value must be payload_b64.mac_b64 (exactly two dot-separated parts)."""
    claims = _make_claims()
    value = build_cookie_value(claims, hmac_secret=SECRET, issued_at=NOW)
    assert value.count(".") == 1
