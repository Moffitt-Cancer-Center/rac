# pattern: Imperative Shell
"""ACA scheduled job entrypoint: nightly Graph sweep for deactivated PIs.

Usage (ACA scheduled job):
    python -m rac_control_plane.cli.graph_sweep

The job:
1. Opens a DB session.
2. Runs run_sweep() — queries deployed apps, batch-looks up PIs in Graph,
   inserts app_ownership_flag rows for deactivated/missing PIs.
3. Commits and logs the summary.
4. Exits 0 on success, 1 on unhandled error (ACA retries per replicaRetryLimit).

Verifies: rac-v1.AC9.2
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from rac_control_plane.data.db import get_session_maker
from rac_control_plane.logging_setup import configure_logging
from rac_control_plane.services.ownership.graph_sweep import run_sweep
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Run the nightly Graph sweep and commit results."""
    settings = get_settings()
    configure_logging(settings)

    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await run_sweep(session)
        await session.commit()

    logger.info(
        "graph_sweep_complete",
        flagged=result.flagged_count,
        skipped=result.skipped_count,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("graph_sweep_failed")
        sys.exit(1)
