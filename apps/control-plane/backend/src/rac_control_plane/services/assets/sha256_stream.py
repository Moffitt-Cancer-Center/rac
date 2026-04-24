# pattern: Functional Core
"""rac_control_plane.services.assets.sha256_stream — streaming sha256 utilities.

Pure functions over byte iterators. No I/O is initiated here; callers supply
the iterator (e.g. from httpx streams, aiofiles, or in-memory bytes). This
keeps the hashing logic testable without any infrastructure.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterable


def stream_sha256(chunks: Iterable[bytes]) -> tuple[str, int]:
    """Consume a byte iterable exactly once; return (hex_digest, total_bytes).

    Works with any Iterable[bytes]: lists, generators, file-like iterators.
    The function does not open files or make network calls — that is the
    caller's responsibility (Imperative Shell).
    """
    h = hashlib.sha256()
    total = 0
    for chunk in chunks:
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest(), total


async def astream_sha256(chunks: AsyncIterator[bytes]) -> tuple[str, int]:
    """Async variant for httpx async streams or aiofiles async reads.

    Pure in the FCIS sense: this function does not initiate I/O; it consumes
    a caller-provided async iterator. The Imperative Shell owns the httpx
    client or file handle lifecycle.
    """
    h = hashlib.sha256()
    total = 0
    async for chunk in chunks:
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest(), total
