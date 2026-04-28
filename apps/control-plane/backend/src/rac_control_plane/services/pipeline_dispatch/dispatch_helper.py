# pattern: Imperative Shell
"""Shared dispatch helper for the build-and-scan pipeline.

Single entry point for both the original-create flow (services/submissions/
create.py) and the admin-retry flow (services/pipeline_dispatch/retry.py).
Mints the per-submission HMAC callback secret in the dedicated pipeline KV,
builds the dispatch payload, and POSTs repository_dispatch.

Design constraints:
  - DispatchUnavailableError is raised BEFORE any I/O when required config is
    missing; this guarantees no orphan KV secret and no DB row left behind.
  - Callback secrets ALWAYS land in settings.pipeline_kv_uri, never in
    settings.kv_uri (the platform KV); the strict separation is part of the
    pipeline identity blast-radius isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

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


async def dispatch_for_submission(
    submission: Submission,
    *,
    settings: Any,
    triggered_by: Literal["submission_created", "admin_retry"],
) -> dict[str, str]:
    """Mint callback secret, build payload, POST repository_dispatch.

    Args:
        submission: ORM object; caller has already verified state == awaiting_scan.
        settings: Application Settings — needs gh_pat, gh_pipeline_owner,
            gh_pipeline_repo, pipeline_kv_uri, callback_base_url,
            pipeline_timeout_minutes.
        triggered_by: 'submission_created' on first dispatch, 'admin_retry'
            when the admin retry route invokes this. Tagged on the log line.

    Returns:
        {submission_id, callback_url, dispatched_at} (dispatched_at is ISO 8601 UTC).

    Raises:
        DispatchUnavailableError: gh_pat or pipeline_kv_uri unset.
    """
    pat = settings.gh_pat
    if not pat:
        raise DispatchUnavailableError(
            "Pipeline dispatch is not configured: RAC_GH_PAT is unset."
        )
    if not settings.pipeline_kv_uri:
        raise DispatchUnavailableError(
            "Pipeline dispatch is not configured: RAC_PIPELINE_KV_URI is unset."
        )

    auth_token = pat.get_secret_value()

    secret_name, _secret_value = await mint_callback_secret(
        submission.id,
        kv_uri=settings.pipeline_kv_uri,
        expiry_minutes=settings.pipeline_timeout_minutes * 2,
    )

    payload = build_dispatch_payload(
        submission,
        callback_base_url=settings.callback_base_url,
        callback_secret_name=secret_name,
    )

    logger.info(
        "pipeline_dispatched",
        submission_id=str(submission.id),
        triggered_by=triggered_by,
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
