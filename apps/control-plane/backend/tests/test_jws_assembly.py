"""Tests for services/tokens/jws_assembly.py — pure JWS assembly."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rac_control_plane.services.tokens.jws_assembly import (
    assemble_jws,
    base64url_encode,
    build_signing_input,
)


# ---------------------------------------------------------------------------
# Concrete tests
# ---------------------------------------------------------------------------

def test_base64url_encode_no_padding() -> None:
    # b"f" encodes to "Zg==" in standard base64, "Zg" in base64url no-pad
    result = base64url_encode(b"f")
    assert "=" not in result
    assert result == "Zg"


def test_base64url_encode_url_safe() -> None:
    # Ensure no '+' or '/' (standard base64 chars that are not URL-safe)
    data = bytes(range(256))
    result = base64url_encode(data)
    assert "+" not in result
    assert "/" not in result


def test_build_signing_input_returns_str_and_bytes() -> None:
    header = {"alg": "ES256", "typ": "JWT"}
    payload = {"sub": "test", "iat": 1000, "exp": 2000}
    si_str, si_bytes = build_signing_input(header, payload)
    assert isinstance(si_str, str)
    assert isinstance(si_bytes, bytes)
    assert si_bytes == si_str.encode("utf-8")


def test_build_signing_input_format() -> None:
    header = {"alg": "ES256"}
    payload = {"iat": 1}
    si_str, _ = build_signing_input(header, payload)
    parts = si_str.split(".")
    assert len(parts) == 2
    # Each part decodes back to JSON
    h = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    p = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    assert h == header
    assert p == payload


def test_build_signing_input_deterministic() -> None:
    # Same inputs must produce same output (critical for signature reproducibility)
    header = {"typ": "JWT", "alg": "ES256", "kid": "key-1"}
    payload = {"iss": "https://rac", "sub": "reviewer", "iat": 1000, "exp": 2000}
    out1 = build_signing_input(header, payload)
    out2 = build_signing_input(header, payload)
    assert out1 == out2


def test_assemble_jws_format() -> None:
    sig = bytes(32)
    si_str, _ = build_signing_input({"alg": "ES256"}, {"iat": 1})
    token = assemble_jws(si_str, sig)
    parts = token.split(".")
    assert len(parts) == 3


def test_assemble_jws_signature_encodes_correctly() -> None:
    sig = b"\xff\xfe\xfd"
    si_str, _ = build_signing_input({"alg": "ES256"}, {"iat": 1})
    token = assemble_jws(si_str, sig)
    sig_b64 = token.split(".")[2]
    # Decode it back (add padding)
    padded = sig_b64 + "=" * (4 - len(sig_b64) % 4)
    decoded = base64.urlsafe_b64decode(padded)
    assert decoded == sig


# ---------------------------------------------------------------------------
# Property tests: round-trip
# ---------------------------------------------------------------------------

_json_values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.text(max_size=50),
)

_json_dicts = st.dictionaries(
    keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
    values=_json_values,
    min_size=1,
    max_size=8,
)


@given(header=_json_dicts, payload=_json_dicts)
@settings(max_examples=100)
def test_property_signing_input_round_trip(
    header: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Encoded header and payload in the signing input decode back to originals."""
    si_str, _ = build_signing_input(header, payload)
    parts = si_str.split(".")
    assert len(parts) == 2
    decoded_header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    decoded_payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    assert decoded_header == header
    assert decoded_payload == payload


@given(
    header=_json_dicts,
    payload=_json_dicts,
    sig=st.binary(min_size=1, max_size=128),
)
@settings(max_examples=100)
def test_property_jws_has_three_parts(
    header: dict[str, Any],
    payload: dict[str, Any],
    sig: bytes,
) -> None:
    si_str, _ = build_signing_input(header, payload)
    token = assemble_jws(si_str, sig)
    assert len(token.split(".")) == 3


@given(sig=st.binary(min_size=1, max_size=128))
@settings(max_examples=100)
def test_property_base64url_no_padding(sig: bytes) -> None:
    encoded = base64url_encode(sig)
    assert "=" not in encoded
