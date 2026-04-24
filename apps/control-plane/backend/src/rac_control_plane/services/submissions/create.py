# pattern: Imperative Shell
"""Submission creation service orchestrating validation and persistence.

Combines pure slug derivation and validation with database writes.
Optionally triggers the rac-pipeline via GitHub repository_dispatch after
the DB row is committed.
"""


from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.submissions import SubmissionCreateRequest
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import ApprovalEvent, Submission, SubmissionStatus
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.github_validation import validate_repo
from rac_control_plane.services.submissions.slug import derive_slug

logger = structlog.get_logger(__name__)


async def create_submission(
    session: AsyncSession,
    principal: Principal,
    request: SubmissionCreateRequest,
    existing_slugs: set[str],
    *,
    emit_submission_metric: Callable[[str], None] | None = None,
    dispatch_fn: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> Submission:
    """Create a new submission with validation and persistence.

    Orchestrates:
    1. Slug derivation (pure)
    2. GitHub repository validation (impure, raises on error)
    3. Database write with approval_event (impure)
    4. Metric emission (impure, via callback)
    5. Pipeline dispatch trigger (impure, via dispatch_fn callback)

    Args:
        session: SQLAlchemy async session
        principal: Current authenticated principal
        request: Submission creation request
        existing_slugs: Set of already-used slugs
        emit_submission_metric: Optional callback to emit submission metric with status.
                                Called with the submission status string.
        dispatch_fn: Optional async callable that accepts the client_payload dict and
                     triggers the GitHub repository_dispatch event.  When None, no
                     dispatch is attempted (used in legacy code paths and tests that
                     do not exercise the pipeline).

    Returns:
        The created Submission ORM object

    Raises:
        ValidationApiError: If repository not found, validation fails, or payload
                            is too large for GitHub dispatch (pipeline_payload_too_large).
    """
    # Step 1: Derive slug (pure)
    slug = derive_slug(request.paper_title, str(request.github_repo_url), existing_slugs)

    # Step 2: Validate GitHub repository (impure, may raise)
    await validate_repo(
        request.github_repo_url,
        request.git_ref,
        request.dockerfile_path,
    )

    # Step 3: Create submission with validated data (impure)
    submission = Submission(
        slug=slug,
        status=SubmissionStatus.awaiting_scan,
        submitter_principal_id=principal.oid,
        agent_id=principal.agent_id,  # None for user flow, UUID for agent flow
        github_repo_url=str(request.github_repo_url),
        git_ref=request.git_ref,
        dockerfile_path=request.dockerfile_path,
        pi_principal_id=request.pi_principal_id,
        dept_fallback=request.dept_fallback,
        manifest=request.manifest,
    )

    session.add(submission)
    await session.flush()  # Get the server-generated UUIDv7

    # Step 4: Record approval event for submission creation (impure)
    approval_event = ApprovalEvent(
        submission_id=submission.id,
        kind="submission_created",
        actor_principal_id=principal.oid,
        decision=None,
        comment=None,
    )

    session.add(approval_event)
    await session.flush()

    # Step 5: Emit metric for the new submission status (impure, optional)
    if emit_submission_metric:
        emit_submission_metric(submission.status.value)

    # Step 6: Trigger pipeline dispatch if a dispatch function was provided.
    #
    # dispatch_fn is called with the raw client_payload dict.  The caller
    # (api/routes/submissions.py) is responsible for building that payload
    # and scheduling the call via BackgroundTasks so the HTTP response is
    # not blocked.  We expose the hook here so tests can inject a mock.
    if dispatch_fn is not None:
        # Build and invoke the dispatch payload inside the service so that
        # all dispatch logic is centralised.  The session has already been
        # flushed, so submission.id is available.
        from rac_control_plane.services.pipeline_dispatch.payload import (
            build_dispatch_payload,
        )
        from rac_control_plane.settings import get_settings

        settings = get_settings()

        # Build a temporary secret name placeholder — the real secret is
        # minted by the caller (route layer) before the background task runs.
        # The route layer passes a pre-built payload or a partially applied fn.
        # Here we just build the payload dict and pass it to dispatch_fn.
        secret_name_placeholder = f"rac-pipeline-cb-{submission.id}"
        client_payload = build_dispatch_payload(
            submission,
            callback_base_url=settings.callback_base_url,
            callback_secret_name=secret_name_placeholder,
        )

        try:
            await dispatch_fn(client_payload)
        except ValidationApiError:
            # Payload too large — mark submission failed and propagate so
            # the route returns 422 to the user.
            submission.status = SubmissionStatus.pipeline_error
            await session.flush()
            logger.error(
                "pipeline_dispatch_payload_too_large",
                submission_id=str(submission.id),
            )
            raise
        except Exception as exc:
            # 5xx / network error — log, leave submission as awaiting_scan.
            # Operator retries via admin UI (Phase 5).
            logger.error(
                "pipeline_dispatch_failed",
                submission_id=str(submission.id),
                error=str(exc),
            )
            # Do NOT re-raise; still return 201 to the user.

    return submission
