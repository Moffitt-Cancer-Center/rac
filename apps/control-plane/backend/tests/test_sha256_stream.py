"""Tests for services/assets/sha256_stream.py — Functional Core.

Verifies foundation for AC8.1, AC8.2, AC8.3.

Coverage:
- Known vectors: empty, single byte, "abc".
- Two-chunk concatenation equals single-buffer hash.
- Async variant matches sync variant.
- Property: any chunking of a bytestring yields the same digest.
- Property: total_bytes is always the sum of chunk lengths.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from rac_control_plane.services.assets.sha256_stream import astream_sha256, stream_sha256


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _async_gen(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


# ---------------------------------------------------------------------------
# Known test vectors — sync
# ---------------------------------------------------------------------------

def test_empty_input_sync() -> None:
    """SHA-256 of empty string is the well-known constant."""
    digest, total = stream_sha256([])
    assert digest == _sha256_hex(b"")
    assert digest == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert total == 0


def test_single_chunk_abc_sync() -> None:
    """SHA-256('abc') is the well-known ba7816bf... constant."""
    digest, total = stream_sha256([b"abc"])
    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert total == 3


def test_single_byte_sync() -> None:
    digest, total = stream_sha256([b"\x00"])
    assert digest == _sha256_hex(b"\x00")
    assert total == 1


def test_two_chunk_concatenation_matches_single_buffer_sync() -> None:
    prefix = b"prefix"
    suffix = b"suffix"
    digest, total = stream_sha256([prefix, suffix])
    assert digest == _sha256_hex(prefix + suffix)
    assert total == len(prefix) + len(suffix)


def test_generator_input_sync() -> None:
    """Accepts a generator (not just a list) per Iterable[bytes] contract."""
    def gen():  # type: ignore[return]
        yield b"hello"
        yield b" "
        yield b"world"
    digest, total = stream_sha256(gen())
    assert digest == _sha256_hex(b"hello world")
    assert total == 11


# ---------------------------------------------------------------------------
# Known test vectors — async
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_input_async() -> None:
    digest, total = await astream_sha256(_async_gen([]))
    assert digest == _sha256_hex(b"")
    assert total == 0


@pytest.mark.asyncio
async def test_abc_async() -> None:
    digest, total = await astream_sha256(_async_gen([b"abc"]))
    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert total == 3


@pytest.mark.asyncio
async def test_two_chunk_async_matches_single_buffer() -> None:
    prefix = b"prefix"
    suffix = b"suffix"
    digest, total = await astream_sha256(_async_gen([prefix, suffix]))
    assert digest == _sha256_hex(prefix + suffix)
    assert total == len(prefix) + len(suffix)


# ---------------------------------------------------------------------------
# Sync vs Async consistency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_and_async_agree() -> None:
    chunks = [b"chunk1", b"chunk2", b"chunk3"]
    sync_digest, sync_total = stream_sha256(chunks)
    async_digest, async_total = await astream_sha256(_async_gen(chunks))
    assert sync_digest == async_digest
    assert sync_total == async_total


# ---------------------------------------------------------------------------
# Property tests — sync
# ---------------------------------------------------------------------------

@given(data=st.binary(min_size=0, max_size=1024))
@hyp_settings(max_examples=200)
def test_property_single_chunk_matches_hashlib(data: bytes) -> None:
    """stream_sha256([data]) == hashlib.sha256(data).hexdigest() for all data."""
    digest, total = stream_sha256([data])
    assert digest == _sha256_hex(data)
    assert total == len(data)


@given(
    data=st.binary(min_size=0, max_size=512),
    split=st.integers(min_value=0, max_value=512),
)
@hyp_settings(max_examples=200)
def test_property_any_split_yields_same_digest(data: bytes, split: int) -> None:
    """Splitting data at any position yields the same sha256 digest."""
    split = min(split, len(data))
    chunks = [data[:split], data[split:]] if data else []
    if not chunks:
        chunks = [b""]
    digest, total = stream_sha256(chunks)
    assert digest == _sha256_hex(data)
    assert total == len(data)


@given(
    parts=st.lists(st.binary(min_size=0, max_size=64), min_size=0, max_size=10)
)
@hyp_settings(max_examples=200)
def test_property_arbitrary_chunking_yields_same_digest(parts: list[bytes]) -> None:
    """Any chunking of bytes produces the same digest as concatenating first."""
    combined = b"".join(parts)
    digest, total = stream_sha256(parts)
    assert digest == _sha256_hex(combined)
    assert total == len(combined)


@given(
    parts=st.lists(st.binary(min_size=0, max_size=64), min_size=0, max_size=10)
)
@hyp_settings(max_examples=100)
def test_property_total_bytes_is_sum_of_chunk_lengths(parts: list[bytes]) -> None:
    """total_bytes returned equals sum of individual chunk lengths."""
    _, total = stream_sha256(parts)
    assert total == sum(len(p) for p in parts)


# ---------------------------------------------------------------------------
# Property tests — async
# ---------------------------------------------------------------------------

@given(
    parts=st.lists(st.binary(min_size=0, max_size=64), min_size=0, max_size=10)
)
@hyp_settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_async_matches_sync(parts: list[bytes]) -> None:
    """astream_sha256 produces the same result as stream_sha256 for any input."""
    sync_digest, sync_total = stream_sha256(parts)
    async_digest, async_total = await astream_sha256(_async_gen(parts))
    assert sync_digest == async_digest
    assert sync_total == async_total
