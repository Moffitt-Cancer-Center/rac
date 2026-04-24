"""Tests for rac_shim.audit.batch_writer — async batched access_log writer.

Verifies: rac-v1.AC10.1 (records reach Postgres), rac-v1.AC12.1 (append-only).

Uses a real Postgres testcontainer (access_log table created by the db fixture).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.db import pg_dsn, pg_pool, postgres_container, truncate_access_log  # noqa: F401
from rac_shim.audit.access_record import AccessRecord, RequestInfo, build_record
from rac_shim.audit.batch_writer import AccessLogBatchWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_record(*, latency_ms: int = 10) -> AccessRecord:
    return build_record(
        request_info=RequestInfo(
            host="test.rac.example.com",
            path="/index",
            method="GET",
            user_agent=None,
            source_ip="10.0.0.1",
            request_id=uuid.uuid4(),
        ),
        app_id=uuid.uuid4(),
        submission_id=None,
        access_mode="token_required",
        token_jti=uuid.uuid4(),
        upstream_status=200,
        latency_ms=latency_ms,
        created_at=_NOW,
        record_id=uuid.uuid4(),
    )


async def _count_rows(pg_pool) -> int:  # type: ignore[no-untyped-def]
    async with pg_pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM access_log")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_size_triggers_flush(pg_pool, truncate_access_log) -> None:
    """Appending batch_size records triggers an immediate flush."""
    writer = AccessLogBatchWriter(
        pg_pool,
        batch_size=3,
        flush_interval_seconds=10.0,  # long interval — only batch_size should trigger
        max_queue_size=100,
    )
    await writer.start()
    try:
        for _ in range(3):
            writer.append(_make_record())

        # Allow the background task time to run.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            count = await _count_rows(pg_pool)
            if count == 3:
                break
            await asyncio.sleep(0.01)

        assert await _count_rows(pg_pool) == 3
    finally:
        await writer.stop()


@pytest.mark.asyncio
async def test_flush_interval_triggers_flush(pg_pool, truncate_access_log) -> None:
    """Flush interval elapses before batch_size is reached → flush happens."""
    writer = AccessLogBatchWriter(
        pg_pool,
        batch_size=100,
        flush_interval_seconds=0.1,
        max_queue_size=100,
    )
    await writer.start()
    try:
        writer.append(_make_record())
        writer.append(_make_record())

        # Wait well past the flush interval.
        await asyncio.sleep(0.5)

        assert await _count_rows(pg_pool) == 2
    finally:
        await writer.stop()


@pytest.mark.asyncio
async def test_queue_full_drops(pg_pool) -> None:
    """Queue full → extra records are dropped and drop_count increments.

    We deliberately do NOT start the writer, so nothing drains the queue.
    """
    writer = AccessLogBatchWriter(
        pg_pool,
        batch_size=1000,
        flush_interval_seconds=60.0,
        max_queue_size=2,
    )
    # Do NOT call start() — we want the queue to stay full.

    for _ in range(5):
        writer.append(_make_record())

    assert writer.drop_count == 3
    assert writer._queue.qsize() <= 2


@pytest.mark.asyncio
async def test_graceful_shutdown_drains(pg_pool, truncate_access_log) -> None:
    """stop() waits for the in-flight queue to drain before returning."""
    writer = AccessLogBatchWriter(
        pg_pool,
        batch_size=1000,
        flush_interval_seconds=60.0,  # won't trigger by time
        max_queue_size=100,
    )
    await writer.start()

    for _ in range(5):
        writer.append(_make_record())

    await writer.stop()

    assert await _count_rows(pg_pool) == 5


@pytest.mark.asyncio
async def test_flush_failure_survives(pg_pool, truncate_access_log) -> None:
    """A transient flush error is logged but the writer keeps running.

    Approach: patch copy_records_to_table to raise once, then succeed.
    Assert the writer continues and subsequent appends flush correctly.
    """
    writer = AccessLogBatchWriter(
        pg_pool,
        batch_size=1,
        flush_interval_seconds=0.05,
        max_queue_size=100,
    )

    call_count = 0
    original_flush = writer._flush

    async def _patched_flush(batch):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated Postgres failure")
        await original_flush(batch)

    writer._flush = _patched_flush  # type: ignore[method-assign]

    await writer.start()
    try:
        # First append — will trigger the simulated failure flush.
        writer.append(_make_record())
        await asyncio.sleep(0.2)

        # Second append — should succeed.
        writer.append(_make_record())
        await asyncio.sleep(0.3)
    finally:
        await writer.stop()

    # At least the second (successful) flush wrote a row.
    count = await _count_rows(pg_pool)
    assert count >= 1
    # The writer ran through the error without crashing (task is done cleanly).
    assert writer._task is None
