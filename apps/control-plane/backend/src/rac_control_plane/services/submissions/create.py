# pattern: Imperative Shell
"""Submission creation service orchestrating validation and persistence.

Combines pure slug derivation and validation with database writes.
Optionally triggers the rac-pipeline via GitHub repository_dispatch after
the DB row is committed.
"""


from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.submissions import SubmissionCreateRequest
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import (
    ApprovalEvent,
    DetectionFinding,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.github_validation import validate_repo
from rac_control_plane.services.ownership.pi_validation import ValidationResult
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
    detection_fn: (
        Callable[[AsyncSession, Submission], Awaitable[list[DetectionFinding]]] | None
    ) = None,
    validate_pi_fn: Callable[[UUID], Awaitable[ValidationResult]] | None = None,
) -> Submission:
    """Create a new submission with validation and persistence.

    Orchestrates:
    1. PI validation via Microsoft Graph (impure, raises on invalid PI)
    2. Slug derivation (pure)
    3. GitHub repository validation (impure, raises on error)
    4. Database write with approval_event (impure)
    5. Metric emission (impure, via callback)
    6. Pipeline dispatch trigger (impure, via dispatch_fn callback)

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
        validate_pi_fn: Optional async callable that validates the PI OID against
                        Microsoft Graph.  Called with ``request.pi_principal_id``
                        before the GitHub repo check.  When None, PI validation is
                        skipped (used in tests and legacy code paths).

    Returns:
        The created Submission ORM object

    Raises:
        ValidationApiError: If PI is invalid, repository not found, validation fails,
                            or payload is too large for GitHub dispatch.
    """
    # Step 1: Validate PI via Graph (impure, optional, may raise)
    if validate_pi_fn is not None:
        from rac_control_plane.services.ownership.pi_validation import Invalid
        pi_result = await validate_pi_fn(request.pi_principal_id)
        if isinstance(pi_result, Invalid):
            raise ValidationApiError(
                "invalid_pi",
                f"PI {request.pi_principal_id} is not a current Entra principal:"
                f" {pi_result.reason}",
            )

    # Step 2: Derive slug (pure)
    slug = derive_slug(request.paper_title, str(request.github_repo_url), existing_slugs)

    # Step 3: Validate GitHub repository (impure, may raise)
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

    # Step 5: Run detection if a detection function was provided
    if detection_fn is not None:
        try:
            await detection_fn(session, submission)
        except Exception as exc:
            # Detection failures are non-fatal for the submission itself;
            # log the error with a distinct, searchable event name and increment
            # the detection error counter for Log Analytics / Azure Monitor.
            from rac_control_plane.metrics import detection_error_counter
            logger.error(
                "detection_raised_exception_continuing_without_detection",
                submission_id=str(submission.id),
                exc_info=exc,
            )
            detection_error_counter.add(1, {"rule": "unknown"})

    # Step 6: Emit metric for the new submission status (impure, optional)
    if emit_submission_metric:
        emit_submission_metric(submission.status.value)

    # Step 7: Trigger pipeline dispatch if a dispatch function was provided.
    #
    # dispatch_fn is called with the raw client_payload dict.  The caller
    # (api/routes/submissions.py) is responsible for building that payload
    # and scheduling the call via BackgroundTasks so the HTTP response is
    # not blocked.  We expose the hook here so tests can inject a mock.
    #
    # Guard: only dispatch when submission is still awaiting_scan.  If
    # detection transitioned it to needs_user_action the pipeline must NOT
    # launch until the researcher resolves the findings (Critical 2 fix).
    if dispatch_fn is not None and submission.status == SubmissionStatus.awaiting_scan:
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
            # Payload too large — mark submission failed, COMMIT the
            # pipeline_error state so it survives the re-raise, and propagate
            # so the route returns 422 to the user.
            submission.status = SubmissionStatus.pipeline_error
            await session.commit()
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
