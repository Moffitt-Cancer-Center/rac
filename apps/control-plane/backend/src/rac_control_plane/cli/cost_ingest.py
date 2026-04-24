# pattern: Imperative Shell
"""ACA scheduled job entrypoint: nightly cost export ingestion.

Usage (ACA scheduled job):
    python -m rac_control_plane.cli.cost_ingest

The job:
1. Opens a DB session.
2. Runs ingest_daily_cost_exports() — lists unprocessed blobs in the
   'cost-exports' container, parses each CSV, upserts into cost_snapshot_monthly.
3. Commits and logs the summary.
4. Exits 0 on success, 1 on unhandled error (ACA retries per replicaRetryLimit).

Verifies: rac-v1.AC11.2
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from rac_control_plane.data.db import get_session_maker
from rac_control_plane.logging_setup import configure_logging
from rac_control_plane.services.cost.ingest import ingest_daily_cost_exports
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Run the cost ingest job and commit results."""
    settings = get_settings()
    configure_logging(settings)

    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await ingest_daily_cost_exports(session)
        await session.commit()

    logger.info(
        "cost_ingest_job_complete",
        files_processed=result.files_processed,
        rows_upserted=result.rows_upserted,
        untagged_rows_skipped=result.untagged_rows_skipped,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("cost_ingest_job_failed")
        sys.exit(1)
