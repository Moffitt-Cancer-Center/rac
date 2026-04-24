# pattern: Imperative Shell
"""AccessLogBatchWriter: non-blocking enqueue + background flush via COPY.

Contract:
- ``append(record)`` is non-blocking; returns immediately.
- If the queue is full, the record is dropped and ``drop_count`` increments.
- Background task flushes every ``flush_interval_seconds`` OR when the queue
  reaches ``batch_size`` records.
- Uses ``asyncpg copy_records_to_table`` for throughput.
- Graceful shutdown: ``start()`` / ``stop()`` pair; ``stop()`` drains the queue.

Verifies: rac-v1.AC10.1, rac-v1.AC12.1.
"""
from __future__ import annotations

import asyncio

import structlog
from asyncpg import Pool

from rac_shim.audit.access_record import AccessRecord

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_COLUMNS = (
    "id",
    "app_id",
    "submission_id",
    "reviewer_token_jti",
    "access_mode",
    "host",
    "path",
    "method",
    "upstream_status",
    "latency_ms",
    "user_agent",
    "source_ip",
    "created_at",
    "request_id",
)


class AccessLogBatchWriter:
    """Writes ``AccessRecord`` rows to ``access_log`` via asyncpg COPY batches."""

    def __init__(
        self,
        pg_pool: Pool,
        *,
        batch_size: int = 5000,
        flush_interval_seconds: float = 2.0,
        max_queue_size: int = 50_000,
    ) -> None:
        self._pool = pg_pool
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._queue: asyncio.Queue[AccessRecord] = asyncio.Queue(maxsize=max_queue_size)
        self._drop_count = 0
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """Start the background flush loop."""
        self._stopping.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Signal the background loop to stop and wait for it to drain."""
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None

    def append(self, record: AccessRecord) -> None:
        """Non-blocking enqueue.  Drops and warns on queue full."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._drop_count += 1
            logger.warning(
                "access_log_queue_full_dropped",
                drop_count=self._drop_count,
            )

    @property
    def drop_count(self) -> int:
        """Number of records dropped due to queue back-pressure."""
        return self._drop_count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Background loop: collect batches and flush to Postgres."""
        try:
            while not self._stopping.is_set():
                batch: list[AccessRecord] = []
                deadline = asyncio.get_event_loop().time() + self._flush_interval
                while len(batch) < self._batch_size:
                    timeout = max(0.0, deadline - asyncio.get_event_loop().time())
                    try:
                        record = await asyncio.wait_for(
                            self._queue.get(), timeout=timeout
                        )
                    except TimeoutError:
                        break
                    batch.append(record)
                    if self._stopping.is_set():
                        break
                if batch:
                    try:
                        await self._flush(batch)
                    except Exception as exc:
                        logger.error(
                            "access_log_flush_error",
                            error=str(exc),
                            batch_size=len(batch),
                        )
        except asyncio.CancelledError:
            pass

        # Drain remaining records on shutdown.
        drain: list[AccessRecord] = []
        while not self._queue.empty():
            drain.append(self._queue.get_nowait())
        if drain:
            try:
                await self._flush(drain)
            except Exception as exc:
                logger.error(
                    "access_log_drain_flush_error",
                    error=str(exc),
                    batch_size=len(drain),
                )

    async def _flush(self, batch: list[AccessRecord]) -> None:
        """Write a batch of records to ``access_log`` via asyncpg COPY."""
        rows = [
            (
                r.id,
                r.app_id,
                r.submission_id,
                r.reviewer_token_jti,
                r.access_mode,
                r.host,
                r.path,
                r.method,
                r.upstream_status,
                r.latency_ms,
                r.user_agent,
                r.source_ip,
                r.created_at,
                r.request_id,
            )
            for r in batch
        ]
        try:
            async with self._pool.acquire() as conn:
                await conn.copy_records_to_table(
                    "access_log",
                    records=rows,
                    columns=list(_COLUMNS),
                )
        except Exception as exc:
            # Do NOT let a flush failure crash the background loop.
            # Log and drop the batch — the shim remains operational.
            logger.error(
                "access_log_flush_failed",
                error=str(exc),
                batch_size=len(batch),
            )
