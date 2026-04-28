# pattern: Imperative Shell
"""Re-dispatch the build-and-scan pipeline for a stuck submission.

Used by the admin retry endpoint to recover submissions that are stuck in
awaiting_scan because the original dispatch was skipped (no PAT) or failed
in flight (e.g. transient GitHub API error).

Mints a fresh callback secret each time. A future tightening can lookup the
KV secret first and reuse if still valid; today the original create-submission
flow does not mint at all (it uses a placeholder name), so on retry there
is never an existing secret to reuse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from rac_control_plane.data.models import Submission
from rac_control_plane.services.pipeline_dispatch import github as gh_dispatch
from rac_control_plane.services.pipeline_dispatch.payload import build_dispatch_payload
from rac_control_plane.services.pipeline_dispatch.secret_mint import mint_callback_secret

logger = structlog.get_logger(__name__)


class DispatchUnavailableError(Exception):
    """Pipeline dispatch can't run because a required input is missing.

    The route layer maps this to HTTP 503. Carries a public-safe message.
    """


async def retry_dispatch(
    submission: Submission,
    *,
    settings: Any,
    admin_oid: str,
) -> dict[str, str]:
    """Re-dispatch the build-and-scan pipeline for one submission.

    Args:
        submission: ORM object; caller has already verified state == awaiting_scan.
        settings: Application Settings — needs gh_pat, gh_pipeline_owner,
            gh_pipeline_repo, kv_uri, callback_base_url, pipeline_timeout_minutes.
        admin_oid: OID of the admin invoking the retry, for the audit log.

    Returns:
        dict with submission_id, callback_url, dispatched_at (ISO 8601).

    Raises:
        DispatchUnavailableError: PAT missing or other config gap.
    """
    pat = settings.gh_pat
    if not pat:
        raise DispatchUnavailableError(
            "Pipeline dispatch is not configured: RAC_GH_PAT is unset."
        )
    auth_token = pat.get_secret_value()

    secret_name, _secret_value = await mint_callback_secret(
        submission.id,
        kv_uri=settings.kv_uri,
        expiry_minutes=settings.pipeline_timeout_minutes * 2,
    )

    payload = build_dispatch_payload(
        submission,
        callback_base_url=settings.callback_base_url,
        callback_secret_name=secret_name,
    )

    logger.info(
        "pipeline_dispatch_retry",
        submission_id=str(submission.id),
        admin_oid=admin_oid,
    )

    await gh_dispatch.dispatch(
        settings.gh_pipeline_owner,
        settings.gh_pipeline_repo,
        payload,
        auth_token=auth_token,
    )

    return {
        "submission_id": str(submission.id),
        "callback_url": payload["callback_url"],
        "dispatched_at": datetime.now(tz=UTC).isoformat(),
    }
