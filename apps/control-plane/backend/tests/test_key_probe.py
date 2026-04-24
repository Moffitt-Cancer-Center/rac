"""Tests for services/tokens/key_probe.py — Key Vault signature format probe."""

from __future__ import annotations

import pytest

from rac_control_plane.services.tokens.key_probe import (
    SignatureFormat,
    _reset_for_tests,
    detect_signature_format,
    get_detected_format,
)


@pytest.fixture(autouse=True)
def reset_format_cache() -> None:
    """Reset the module-level cache before each test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


async def test_64_byte_output_detected_as_raw() -> None:
    """A 64-byte signature is classified as RAW_R_S."""
    call_count = 0

    async def fake_signer(digest: bytes) -> bytes:
        nonlocal call_count
        call_count += 1
        return bytes(64)

    fmt = await detect_signature_format(fake_signer)
    assert fmt == SignatureFormat.RAW_R_S
    assert call_count == 1


async def test_70_byte_output_detected_as_der() -> None:
    """A 70-byte signature is classified as DER."""
    async def fake_signer(digest: bytes) -> bytes:
        return bytes(70)

    fmt = await detect_signature_format(fake_signer)
    assert fmt == SignatureFormat.DER


async def test_72_byte_output_detected_as_der() -> None:
    """A 72-byte signature (common DER for ES256) is classified as DER."""
    async def fake_signer(digest: bytes) -> bytes:
        return bytes(72)

    fmt = await detect_signature_format(fake_signer)
    assert fmt == SignatureFormat.DER


async def test_cached_between_calls() -> None:
    """Second call returns cached value without re-invoking the signer."""
    call_count = 0

    async def counting_signer(digest: bytes) -> bytes:
        nonlocal call_count
        call_count += 1
        return bytes(64)

    fmt1 = await detect_signature_format(counting_signer)
    fmt2 = await detect_signature_format(counting_signer)
    assert fmt1 == fmt2 == SignatureFormat.RAW_R_S
    assert call_count == 1, "Signer must be called only once (cached)"


def test_get_detected_format_raises_before_detection() -> None:
    """get_detected_format() raises RuntimeError before detect_signature_format runs."""
    with pytest.raises(RuntimeError, match="not been detected"):
        get_detected_format()


async def test_get_detected_format_after_detection() -> None:
    """get_detected_format() returns the cached value after detection."""
    async def fake_signer(digest: bytes) -> bytes:
        return bytes(64)

    await detect_signature_format(fake_signer)
    assert get_detected_format() == SignatureFormat.RAW_R_S


def test_reset_for_tests_clears_cache() -> None:
    """_reset_for_tests() clears the cache so get_detected_format raises."""
    # The autouse fixture already reset it; calling again is idempotent.
    _reset_for_tests()
    with pytest.raises(RuntimeError):
        get_detected_format()
