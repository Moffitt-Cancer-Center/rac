"""Tests for rac_shim.token.validation — pure JWT verification.

Verifies: rac-v1.AC7.1, AC7.3, AC7.4, AC7.6
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from joserfc import jwt
from joserfc.jwk import ECKey

from rac_shim.token.errors import (
    Expired,
    Malformed,
    NotYetValid,
    SignatureInvalid,
    WrongAudience,
    WrongIssuer,
)
from rac_shim.token.validation import decode_unverified_header, verify_signature_and_claims

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ISSUER = "https://rac.example.com"
AUDIENCE = "rac-app:myapp"


def _mint(
    key: ECKey,
    *,
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    sub: str = "reviewer-1",
    jti: str | None = None,
    iat: int = 1_700_000_000,
    exp: int = 9_999_999_999,
    nbf: int | None = None,
    scope: str = "read",
) -> str:
    payload: dict[str, object] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "jti": jti or str(uuid.uuid4()),
        "iat": iat,
        "exp": exp,
        "scope": scope,
    }
    if nbf is not None:
        payload["nbf"] = nbf
    return jwt.encode({"alg": "ES256"}, payload, key)


@pytest.fixture
def keypair() -> ECKey:
    return ECKey.generate_key("P-256", auto_kid=True)


@pytest.fixture
def other_keypair() -> ECKey:
    return ECKey.generate_key("P-256", auto_kid=True)


NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
NOW_TS = int(NOW.timestamp())

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_valid_token(keypair: ECKey) -> None:
    jti = str(uuid.uuid4())
    token = _mint(keypair, jti=jti, iat=NOW_TS - 60, exp=NOW_TS + 3600)
    claims = verify_signature_and_claims(
        token,
        public_key=keypair,
        expected_issuer=ISSUER,
        expected_audience=AUDIENCE,
        now=NOW,
    )
    assert claims.iss == ISSUER
    assert claims.aud == AUDIENCE
    assert claims.jti == uuid.UUID(jti)
    assert claims.sub == "reviewer-1"
    assert claims.scope == "read"


def test_expired_token(keypair: ECKey) -> None:
    """AC7.3: expired token raises Expired."""
    token = _mint(keypair, iat=NOW_TS - 7200, exp=NOW_TS - 3600)
    with pytest.raises(Expired):
        verify_signature_and_claims(
            token,
            public_key=keypair,
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            now=NOW,
        )


def test_wrong_issuer(keypair: ECKey) -> None:
    token = _mint(keypair, iss="https://evil.example.com")
    with pytest.raises(WrongIssuer):
        verify_signature_and_claims(
            token,
            public_key=keypair,
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            now=NOW,
        )


def test_wrong_audience_app_a_vs_app_b(keypair: ECKey) -> None:
    """AC7.6: token minted for app-a is rejected when checked against app-b."""
    token = _mint(keypair, aud="rac-app:app-a")
    with pytest.raises(WrongAudience):
        verify_signature_and_claims(
            token,
            public_key=keypair,
            expected_issuer=ISSUER,
            expected_audience="rac-app:app-b",
            now=NOW,
        )


def test_wrong_audience_takes_priority_over_expired(keypair: ECKey) -> None:
    """AC7.6 ordering guard: a wrong-audience token that is ALSO expired must
    raise WrongAudience (checked first), not Expired. This ensures a refactor
    cannot reorder the checks in a way that silently leaks which failure
    reason is 'responsible'."""
    token = _mint(keypair, aud="rac-app:app-a", exp=NOW_TS - 3600)
    with pytest.raises(WrongAudience):
        verify_signature_and_claims(
            token,
            public_key=keypair,
            expected_issuer=ISSUER,
            expected_audience="rac-app:app-b",
            now=NOW,
        )


def test_malformed_garbage(keypair: ECKey) -> None:
    """AC7.4: completely invalid string raises Malformed."""
    with pytest.raises(Malformed):
        verify_signature_and_claims(
            "not.a.jwt",
            public_key=keypair,
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            now=NOW,
        )


def test_malformed_two_parts(keypair: ECKey) -> None:
    with pytest.raises(Malformed):
        decode_unverified_header("header.payload")


def test_signature_invalid_wrong_key(keypair: ECKey, other_keypair: ECKey) -> None:
    """Token signed with key A must fail when verified with key B."""
    token = _mint(keypair)
    with pytest.raises(SignatureInvalid):
        verify_signature_and_claims(
            token,
            public_key=other_keypair,
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            now=NOW,
        )


def test_not_yet_valid(keypair: ECKey) -> None:
    """nbf in the future → NotYetValid."""
    future_nbf = NOW_TS + 3600
    token = _mint(keypair, nbf=future_nbf, exp=NOW_TS + 7200)
    with pytest.raises(NotYetValid):
        verify_signature_and_claims(
            token,
            public_key=keypair,
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            now=NOW,
        )


def test_nbf_in_past_accepted(keypair: ECKey) -> None:
    """nbf in the past is fine."""
    past_nbf = NOW_TS - 60
    token = _mint(keypair, nbf=past_nbf, exp=NOW_TS + 3600)
    claims = verify_signature_and_claims(
        token,
        public_key=keypair,
        expected_issuer=ISSUER,
        expected_audience=AUDIENCE,
        now=NOW,
    )
    assert claims.nbf is not None


def test_decode_unverified_header_returns_alg(keypair: ECKey) -> None:
    token = _mint(keypair)
    hdr = decode_unverified_header(token)
    assert hdr["alg"] == "ES256"


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# Use a module-level keypair to avoid regenerating EC keys on every example
_PROP_KEY = ECKey.generate_key("P-256", auto_kid=True)

# Epoch bounds: 2000-01-01 to 2099-12-31 to avoid datetime overflow
EPOCH_MIN = 946_684_800  # 2000-01-01 UTC
EPOCH_MAX = 4_102_444_799  # 2099-12-31 UTC


@given(
    now_ts=st.integers(min_value=EPOCH_MIN, max_value=EPOCH_MAX - 1),
    offset=st.integers(min_value=1, max_value=86400 * 365),
)
@settings(max_examples=50)
def test_property_valid_exp_succeeds(now_ts: int, offset: int) -> None:
    """For any (now, exp) with exp > now, validation succeeds (other claims valid)."""
    exp_ts = now_ts + offset
    assume(exp_ts <= EPOCH_MAX)
    jti = str(uuid.uuid4())
    token = _mint(
        _PROP_KEY,
        jti=jti,
        iat=now_ts - 1,
        exp=exp_ts,
    )
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    claims = verify_signature_and_claims(
        token,
        public_key=_PROP_KEY,
        expected_issuer=ISSUER,
        expected_audience=AUDIENCE,
        now=now,
    )
    assert claims.jti == uuid.UUID(jti)


@given(
    now_ts=st.integers(min_value=EPOCH_MIN + 1, max_value=EPOCH_MAX),
    offset=st.integers(min_value=0, max_value=86400 * 365),
)
@settings(max_examples=50)
def test_property_expired_raises(now_ts: int, offset: int) -> None:
    """For any (now, exp) with exp <= now, validation raises Expired."""
    exp_ts = now_ts - offset  # exp is at or before now
    assume(exp_ts >= EPOCH_MIN)
    token = _mint(
        _PROP_KEY,
        iat=exp_ts - 1,
        exp=exp_ts,
    )
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    with pytest.raises(Expired):
        verify_signature_and_claims(
            token,
            public_key=_PROP_KEY,
            expected_issuer=ISSUER,
            expected_audience=AUDIENCE,
            now=now,
        )
