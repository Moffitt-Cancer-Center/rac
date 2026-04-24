# pattern: Imperative Shell
"""Key Vault public key cache with 5-minute TTL."""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any

from azure.keyvault.keys.aio import KeyClient
from joserfc.jwk import ECKey


@dataclass
class _CacheEntry:
    key: ECKey
    expires_at: float  # monotonic seconds


def _bytes_to_b64url(value: bytes) -> str:
    """Base64url-encode raw bytes (no padding) for JWK x/y fields."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _kv_key_to_eckey(kv_key: Any) -> ECKey:
    """Convert a Key Vault KeyVaultKey → joserfc ECKey (public only).

    ``kv_key.key`` is an azure.keyvault.keys.JsonWebKey where:
    - ``kty`` is a KeyType enum; use ``.value`` to get the string ("EC").
    - ``crv`` is a KeyCurveName enum; use ``.value`` to get the string ("P-256").
    - ``x`` and ``y`` are raw ``bytes``; must be base64url-encoded for JWK.

    Rejects any key that has a ``d`` component (private key material should
    never be fetched from Key Vault public-key reads).
    """
    jwk_mat = kv_key.key
    if getattr(jwk_mat, "d", None) is not None:
        raise ValueError("Key Vault returned private key material — refusing to load.")

    kty = jwk_mat.kty
    crv = jwk_mat.crv
    # Handle both enum objects (with .value) and plain strings.
    kty_str = kty.value if hasattr(kty, "value") else str(kty)
    crv_str = crv.value if hasattr(crv, "value") else str(crv)

    x_raw: bytes = jwk_mat.x
    y_raw: bytes = jwk_mat.y

    jwk_dict: dict[str, str | list[str]] = {
        "kty": kty_str,
        "crv": crv_str,
        "x": _bytes_to_b64url(x_raw),
        "y": _bytes_to_b64url(y_raw),
    }
    return ECKey.import_key(jwk_dict)


class KeyVaultPublicKeyCache:
    """Thread-safe, TTL-based in-memory cache for EC public keys from Key Vault.

    Uses a per-key asyncio.Lock to ensure that concurrent cache misses for the
    same key_name result in exactly one Key Vault fetch (double-checked locking).
    """

    def __init__(
        self,
        kv_uri: str,
        credential: Any,
        *,
        ttl_seconds: int = 300,
        client: KeyClient | None = None,
    ) -> None:
        self._kv_uri = kv_uri
        self._credential = credential
        self._ttl = ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._master_lock = asyncio.Lock()
        self._client: KeyClient = client or KeyClient(vault_url=kv_uri, credential=credential)

    async def get_jwk(self, key_name: str) -> ECKey:
        """Return the cached public key, fetching from Key Vault on a miss or TTL expiry.

        The per-key lock ensures concurrent callers deduplicate inflight fetches.
        """
        # Fast path: cache hit and still fresh.
        now = time.monotonic()
        entry = self._cache.get(key_name)
        if entry and entry.expires_at > now:
            return entry.key

        # Slow path: acquire the per-key lock, then re-check under the lock.
        lock = await self._lock_for(key_name)
        async with lock:
            entry = self._cache.get(key_name)
            now = time.monotonic()
            if entry and entry.expires_at > now:
                return entry.key
            kv_key = await self._client.get_key(key_name)
            jwk = _kv_key_to_eckey(kv_key)
            self._cache[key_name] = _CacheEntry(key=jwk, expires_at=now + self._ttl)
            return jwk

    async def _lock_for(self, key_name: str) -> asyncio.Lock:
        """Return (or create) the asyncio.Lock for the given key_name."""
        async with self._master_lock:
            if key_name not in self._locks:
                self._locks[key_name] = asyncio.Lock()
            return self._locks[key_name]
