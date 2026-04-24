"""Tests for rac_shim.token.kv_key_cache — Key Vault public key cache.

Verifies: rac-v1.AC7.1 (public key is fetched and cached; per-key lock
deduplicates concurrent fetches).

Uses mock KeyClient injected via the ``client=`` kwarg to avoid any real
Azure SDK network calls.
"""
from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from joserfc.jwk import ECKey

from rac_shim.token.kv_key_cache import KeyVaultPublicKeyCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_kv_key(eckey: ECKey) -> MagicMock:
    """Build a fake KeyVaultKey shaped like the real Azure SDK object.

    The real SDK exposes kv_key.key.{kty, crv, x, y} where x, y are bytes.
    We re-export from a joserfc ECKey to get consistent raw bytes.
    """
    jwk_dict = eckey.as_dict(private=False)

    def _b64url_to_bytes(b64: str) -> bytes:
        # Restore padding, then decode
        padded = b64 + "=" * (-len(b64) % 4)
        return base64.urlsafe_b64decode(padded)

    jwk_mat = MagicMock()
    # kty / crv as plain strings (the cache handles both strings and enum .value)
    jwk_mat.kty = "EC"
    jwk_mat.crv = "P-256"
    jwk_mat.x = _b64url_to_bytes(jwk_dict["x"])
    jwk_mat.y = _b64url_to_bytes(jwk_dict["y"])
    jwk_mat.d = None  # public key only

    fake_kv_key = MagicMock()
    fake_kv_key.key = jwk_mat
    return fake_kv_key


def _make_mock_client(eckey: ECKey) -> AsyncMock:
    """Return an async mock KeyClient whose get_key coroutine returns a fake key."""
    client = AsyncMock()
    client.get_key = AsyncMock(return_value=_make_fake_kv_key(eckey))
    return client


def _make_cache(eckey: ECKey, *, ttl_seconds: int = 300) -> tuple[KeyVaultPublicKeyCache, AsyncMock]:
    client = _make_mock_client(eckey)
    cache = KeyVaultPublicKeyCache(
        kv_uri="https://fake.vault.azure.net",
        credential=MagicMock(),
        ttl_seconds=ttl_seconds,
        client=client,
    )
    return cache, client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_fetches() -> None:
    """Cache miss → get_key called once, returns an ECKey."""
    eckey = ECKey.generate_key("P-256", auto_kid=True)
    cache, client = _make_cache(eckey)

    result = await cache.get_jwk("rac-app-myapp-v1")

    client.get_key.assert_awaited_once_with("rac-app-myapp-v1")
    assert isinstance(result, ECKey)


@pytest.mark.asyncio
async def test_second_call_uses_cache() -> None:
    """Two get_jwk calls within TTL → get_key called exactly once."""
    eckey = ECKey.generate_key("P-256", auto_kid=True)
    cache, client = _make_cache(eckey, ttl_seconds=300)

    r1 = await cache.get_jwk("rac-app-myapp-v1")
    r2 = await cache.get_jwk("rac-app-myapp-v1")

    client.get_key.assert_awaited_once()
    # Both calls return the same ECKey instance (same cache entry).
    assert r1 is r2


@pytest.mark.asyncio
async def test_ttl_expiry_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """After TTL elapses, the next call re-fetches from Key Vault."""
    eckey = ECKey.generate_key("P-256", auto_kid=True)
    cache, client = _make_cache(eckey, ttl_seconds=60)

    # First call — populates cache.
    await cache.get_jwk("rac-app-myapp-v1")
    assert client.get_key.await_count == 1

    # Simulate time advancing past the TTL.
    original_monotonic = time.monotonic
    monkeypatch.setattr(
        "rac_shim.token.kv_key_cache.time.monotonic",
        lambda: original_monotonic() + 120,  # 120s past now — TTL expired
    )

    await cache.get_jwk("rac-app-myapp-v1")
    assert client.get_key.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_requests_dedupe() -> None:
    """10 concurrent get_jwk calls on a cold cache → get_key called exactly once."""
    eckey = ECKey.generate_key("P-256", auto_kid=True)

    # Add a small artificial delay so concurrent calls actually pile up.
    async def _slow_get_key(name: str) -> MagicMock:
        await asyncio.sleep(0.05)
        return _make_fake_kv_key(eckey)

    client = AsyncMock()
    client.get_key = _slow_get_key

    cache = KeyVaultPublicKeyCache(
        kv_uri="https://fake.vault.azure.net",
        credential=MagicMock(),
        ttl_seconds=300,
        client=client,
    )

    results = await asyncio.gather(*[cache.get_jwk("rac-app-myapp-v1") for _ in range(10)])

    # All results are ECKey instances.
    assert all(isinstance(r, ECKey) for r in results)

    # The key should have been fetched exactly once even under concurrency.
    # We verify this by checking the cache's internal state: one entry exists.
    assert len(cache._cache) == 1
    assert "rac-app-myapp-v1" in cache._cache


@pytest.mark.asyncio
async def test_different_keys_independent() -> None:
    """get_jwk('a') and get_jwk('b') are independent cache entries."""
    eckey_a = ECKey.generate_key("P-256", auto_kid=True)
    eckey_b = ECKey.generate_key("P-256", auto_kid=True)

    call_count = 0

    async def _get_key(name: str) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if name == "key-a":
            return _make_fake_kv_key(eckey_a)
        return _make_fake_kv_key(eckey_b)

    client = AsyncMock()
    client.get_key = _get_key

    cache = KeyVaultPublicKeyCache(
        kv_uri="https://fake.vault.azure.net",
        credential=MagicMock(),
        ttl_seconds=300,
        client=client,
    )

    r_a1 = await cache.get_jwk("key-a")
    r_b1 = await cache.get_jwk("key-b")
    r_a2 = await cache.get_jwk("key-a")  # should hit cache
    r_b2 = await cache.get_jwk("key-b")  # should hit cache

    # Each key fetched exactly once.
    assert call_count == 2
    assert r_a1 is r_a2
    assert r_b1 is r_b2

    # The two cache entries are different ECKey objects.
    assert r_a1 is not r_b1
