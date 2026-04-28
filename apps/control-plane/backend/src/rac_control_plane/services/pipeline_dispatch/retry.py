# pattern: Imperative Shell
"""Re-dispatch the build-and-scan pipeline for a stuck submission.

Used by the admin retry endpoint to recover submissions stuck in
awaiting_scan. Delegates to dispatch_for_submission for the dispatch
sequence; adds the admin_oid audit log line on top.
"""

from __future__ import annotations

from typing import Any

import structlog

from rac_control_plane.data.models import Submission
from rac_control_plane.services.pipeline_dispatch.dispatch_helper import (
    DispatchUnavailableError as DispatchUnavailableError,
    dispatch_for_submission,
)

logger = structlog.get_logger(__name__)


async def retry_dispatch(
    submission: Submission,
    *,
    settings: Any,
    admin_oid: str,
) -> dict[str, str]:
    """Re-dispatch the build-and-scan pipeline for one submission (admin op).

    Args:
        submission: ORM object; caller has already verified state == awaiting_scan.
        settings: Application Settings (see dispatch_for_submission).
        admin_oid: OID of the admin invoking the retry, for the audit log.

    Returns:
        See dispatch_for_submission.

    Raises:
        DispatchUnavailableError: re-raised from the helper.
    """
    logger.info(
        "pipeline_dispatch_retry_admin",
        submission_id=str(submission.id),
        admin_oid=admin_oid,
    )
    return await dispatch_for_submission(
        submission,
        settings=settings,
        triggered_by="admin_retry",
    )
