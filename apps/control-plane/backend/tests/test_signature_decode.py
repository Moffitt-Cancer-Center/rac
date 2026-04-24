"""Tests for services/tokens/signature_decode.py — DER-to-raw r||s conversion."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from hypothesis import given, settings
from hypothesis import strategies as st

from rac_control_plane.services.tokens.signature_decode import der_to_raw_r_s


# ---------------------------------------------------------------------------
# Concrete tests
# ---------------------------------------------------------------------------

def test_known_vector_32_byte_coords() -> None:
    """Round-trip a known r||s through encode -> DER -> der_to_raw_r_s."""
    r = int.from_bytes(os.urandom(32), "big")
    s = int.from_bytes(os.urandom(32), "big")
    der = encode_dss_signature(r, s)
    raw = der_to_raw_r_s(der, coord_size=32)
    assert len(raw) == 64
    assert raw[:32] == r.to_bytes(32, "big")
    assert raw[32:] == s.to_bytes(32, "big")


def test_output_length_is_64_for_p256() -> None:
    r = 0x4E97E3A9B765CF95CF6B3C82C49E0DA8E0C49B2B2DAC65E8AA4BD48B96D0F9D5
    s = 0x1A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6D7E8F9A0B1C2D3E4F5A6B7C8D9E0F1A2
    der = encode_dss_signature(r, s)
    raw = der_to_raw_r_s(der)
    assert len(raw) == 64


def test_r_and_s_correctly_extracted() -> None:
    r = 1
    s = 2
    der = encode_dss_signature(r, s)
    raw = der_to_raw_r_s(der, coord_size=32)
    assert raw == (1).to_bytes(32, "big") + (2).to_bytes(32, "big")


def test_48_byte_coords_for_p384() -> None:
    r = int.from_bytes(os.urandom(48), "big")
    s = int.from_bytes(os.urandom(48), "big")
    der = encode_dss_signature(r, s)
    raw = der_to_raw_r_s(der, coord_size=48)
    assert len(raw) == 96


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# P-256 max coord value (order of P-256)
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

_coords = st.integers(min_value=1, max_value=_P256_ORDER - 1)


@given(r=_coords, s=_coords)
@settings(max_examples=100)
def test_property_round_trip_32_bytes(r: int, s: int) -> None:
    """For any valid P-256 r, s: encode DER → der_to_raw_r_s → r||s matches."""
    der = encode_dss_signature(r, s)
    raw = der_to_raw_r_s(der, coord_size=32)
    assert len(raw) == 64
    assert raw[:32] == r.to_bytes(32, "big")
    assert raw[32:] == s.to_bytes(32, "big")


@given(r=_coords, s=_coords, coord_size=st.integers(min_value=1, max_value=66))
@settings(max_examples=50)
def test_property_output_length_is_2x_coord_size(r: int, s: int, coord_size: int) -> None:
    """Output length is always exactly 2 * coord_size."""
    # Only test when r and s fit in coord_size bytes
    max_val = (1 << (coord_size * 8)) - 1
    if r > max_val or s > max_val:
        return  # skip combinations where coord doesn't fit
    der = encode_dss_signature(r, s)
    raw = der_to_raw_r_s(der, coord_size=coord_size)
    assert len(raw) == 2 * coord_size
