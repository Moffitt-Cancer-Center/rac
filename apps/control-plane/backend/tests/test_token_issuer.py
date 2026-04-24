"""Tests for services/tokens/issuer.py — reviewer token issuance.

Uses a local ES256 keypair via the cryptography library to avoid real Key Vault.
The signer callable signs with the private key directly.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)
from sqlalchemy import select

from rac_control_plane.data.models import ApprovalEvent, ReviewerToken
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.tokens.issuer import IssuedToken, issue_reviewer_token
from rac_control_plane.services.tokens.key_probe import SignatureFormat, _reset_for_tests


# ---------------------------------------------------------------------------
# Fixtures: local ES256 keypair
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ec_private_key() -> ec.EllipticCurvePrivateKey:
    """Generate a fresh P-256 private key for testing."""
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def ec_public_key(ec_private_key: ec.EllipticCurvePrivateKey) -> ec.EllipticCurvePublicKey:
    return ec_private_key.public_key()


def make_raw_signer(
    private_key: ec.EllipticCurvePrivateKey,
) -> object:
    """Return an async signer that signs a digest and returns raw r||s (64 bytes)."""
    async def _sign(digest: bytes) -> bytes:
        # Sign the digest — cryptography requires wrapping in Prehashed
        der_sig = private_key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
        r, s = decode_dss_signature(der_sig)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return _sign


def make_der_signer(
    private_key: ec.EllipticCurvePrivateKey,
) -> object:
    """Return an async signer that returns DER-encoded ECDSA bytes."""
    async def _sign(digest: bytes) -> bytes:
        return private_key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    return _sign


def _decode_jws_payload(jws: str) -> dict[str, object]:
    """Decode the payload from a compact JWS without verifying the signature."""
    parts = jws.split(".")
    assert len(parts) == 3
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _verify_jws(
    jws: str,
    public_key: ec.EllipticCurvePublicKey,
) -> bool:
    """Verify the ES256 signature in a compact JWS using the given public key."""
    parts = jws.split(".")
    if len(parts) != 3:
        return False
    signing_input = f"{parts[0]}.{parts[1]}".encode("utf-8")
    sig_b64 = parts[2]
    padded = sig_b64 + "=" * (4 - len(sig_b64) % 4)
    raw_sig = base64.urlsafe_b64decode(padded)
    # raw_sig is r||s; convert to DER for cryptography library
    r = int.from_bytes(raw_sig[:32], "big")
    s = int.from_bytes(raw_sig[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, Prehashed as _Pre
    der_sig = encode_dss_signature(r, s)
    digest = hashlib.sha256(signing_input).digest()
    try:
        public_key.verify(der_sig, digest, ec.ECDSA(_Pre(hashes.SHA256())))
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def reset_format_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_for_tests()
    # Patch get_settings in the issuer module so no real env vars needed
    from tests.conftest_settings_helper import make_test_settings
    settings = make_test_settings()
    monkeypatch.setattr("rac_control_plane.services.tokens.issuer.get_settings", lambda: settings)
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# Helper: create an App row so FK is satisfied
# ---------------------------------------------------------------------------

async def _insert_app(session: object, app_id: UUID, slug: str) -> None:
    from rac_control_plane.data.models import App
    app = App(
        id=app_id,
        slug=slug,
        pi_principal_id=uuid4(),
        dept_fallback="test",
    )
    session.add(app)
    await session.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_issuance_produces_verifiable_jws(
    db_session: object,
    ec_private_key: ec.EllipticCurvePrivateKey,
    ec_public_key: ec.EllipticCurvePublicKey,
) -> None:
    """Issued JWS signature verifies against the test public key."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "testapp")

    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="testapp",
        reviewer_label="Reviewer #1",
        ttl_days=30,
        actor_principal_id=uuid4(),
        signer=make_raw_signer(ec_private_key),
        signature_format=SignatureFormat.RAW_R_S,
        issuer="https://rac.example.com",
    )
    assert isinstance(result, IssuedToken)
    assert _verify_jws(result.jwt, ec_public_key)


async def test_claims_match_request(
    db_session: object,
    ec_private_key: ec.EllipticCurvePrivateKey,
) -> None:
    """Decoded claims in the JWS match the issuance parameters."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "claimsapp")
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="claimsapp",
        reviewer_label="Journal Reviewer #3",
        ttl_days=14,
        actor_principal_id=uuid4(),
        signer=make_raw_signer(ec_private_key),
        signature_format=SignatureFormat.RAW_R_S,
        issuer="https://rac.example.com",
        now=now,
    )
    payload = _decode_jws_payload(result.jwt)
    assert payload["aud"] == "rac-app:claimsapp"
    assert payload["sub"] == "Journal Reviewer #3"
    assert payload["iss"] == "https://rac.example.com"
    assert payload["scope"] == "read"
    exp = int(payload["exp"])  # type: ignore[arg-type]
    iat = int(payload["iat"])  # type: ignore[arg-type]
    assert exp == iat + 14 * 86400


async def test_ttl_exceeds_max_raises(db_session: object, ec_private_key: ec.EllipticCurvePrivateKey) -> None:
    """ttl_days > max raises ValidationApiError."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "ttlapp")
    with pytest.raises(ValidationApiError) as exc_info:
        await issue_reviewer_token(
            db_session,
            app_id=app_id,
            app_slug="ttlapp",
            reviewer_label="R",
            ttl_days=181,
            actor_principal_id=uuid4(),
            signer=make_raw_signer(ec_private_key),
            signature_format=SignatureFormat.RAW_R_S,
        )
    assert exc_info.value.code == "ttl_exceeds_max"


async def test_ttl_at_max_ok(db_session: object, ec_private_key: ec.EllipticCurvePrivateKey) -> None:
    """ttl_days == 180 succeeds."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "maxttlapp")
    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="maxttlapp",
        reviewer_label="R",
        ttl_days=180,
        actor_principal_id=uuid4(),
        signer=make_raw_signer(ec_private_key),
        signature_format=SignatureFormat.RAW_R_S,
    )
    assert result is not None


async def test_inserts_reviewer_token_row(
    db_session: object,
    ec_private_key: ec.EllipticCurvePrivateKey,
) -> None:
    """The reviewer_token row is inserted with correct fields."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "rowapp")
    actor = uuid4()

    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="rowapp",
        reviewer_label="Row Reviewer",
        ttl_days=7,
        actor_principal_id=actor,
        signer=make_raw_signer(ec_private_key),
        signature_format=SignatureFormat.RAW_R_S,
    )

    stmt = select(ReviewerToken).where(ReviewerToken.jti == str(result.jti))
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.app_id == app_id
    assert row.reviewer_label == "Row Reviewer"
    assert row.kid == "rac-app-rowapp-v1"
    assert row.issued_by_principal_id == actor
    assert row.scope == "read"


async def test_inserts_approval_event(
    db_session: object,
    ec_private_key: ec.EllipticCurvePrivateKey,
) -> None:
    """An approval_event with kind='reviewer_token_issued' is inserted."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "evtapp")
    actor = uuid4()

    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="evtapp",
        reviewer_label="Event Reviewer",
        ttl_days=30,
        actor_principal_id=actor,
        signer=make_raw_signer(ec_private_key),
        signature_format=SignatureFormat.RAW_R_S,
    )

    stmt = select(ApprovalEvent).where(
        ApprovalEvent.kind == "reviewer_token_issued",
        ApprovalEvent.actor_principal_id == actor,
    )
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) >= 1
    evt = events[-1]
    assert evt.payload is not None
    assert evt.payload["jti"] == str(result.jti)


async def test_der_format_decoded(
    db_session: object,
    ec_private_key: ec.EllipticCurvePrivateKey,
    ec_public_key: ec.EllipticCurvePublicKey,
) -> None:
    """When signature_format=DER, the DER sig is decoded to raw r||s before assembly."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "derapp")

    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="derapp",
        reviewer_label="DER Reviewer",
        ttl_days=30,
        actor_principal_id=uuid4(),
        signer=make_der_signer(ec_private_key),  # returns DER bytes
        signature_format=SignatureFormat.DER,
    )
    # JWS should still verify — the raw r||s was correctly extracted from DER
    assert _verify_jws(result.jwt, ec_public_key)


async def test_raw_format_passthrough(
    db_session: object,
    ec_private_key: ec.EllipticCurvePrivateKey,
    ec_public_key: ec.EllipticCurvePublicKey,
) -> None:
    """When signature_format=RAW_R_S, the 64 raw bytes are used directly."""
    app_id = uuid4()
    await _insert_app(db_session, app_id, "rawapp")

    result = await issue_reviewer_token(
        db_session,
        app_id=app_id,
        app_slug="rawapp",
        reviewer_label="Raw Reviewer",
        ttl_days=30,
        actor_principal_id=uuid4(),
        signer=make_raw_signer(ec_private_key),  # returns raw 64 bytes
        signature_format=SignatureFormat.RAW_R_S,
    )
    assert _verify_jws(result.jwt, ec_public_key)
