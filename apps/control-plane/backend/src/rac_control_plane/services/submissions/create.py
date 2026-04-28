# pattern: Imperative Shell
"""Submission creation service orchestrating validation and persistence.

Combines pure slug derivation and validation with database writes.
Triggers the rac-pipeline via the dispatch_for_submission helper after
the DB row is committed (unconditional path).
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
from rac_control_plane.manifest.parser import (
    SharedReferenceNotYetSupportedError,
    manifest_from_dict,
    reject_shared_references,
)
from rac_control_plane.manifest.schema import ManifestV1
from rac_control_plane.services.github_validation import validate_repo
from rac_control_plane.services.ownership.pi_validation import ValidationResult
from rac_control_plane.services.pipeline_dispatch.dispatch_helper import (
    DispatchUnavailableError,
    dispatch_for_submission,
)
from rac_control_plane.services.submissions.slug import derive_slug
from rac_control_plane.settings import Settings

logger = structlog.get_logger(__name__)


async def create_submission(
    session: AsyncSession,
    principal: Principal,
    request: SubmissionCreateRequest,
    existing_slugs: set[str],
    *,
    parsed_manifest: ManifestV1 | None = None,
    emit_submission_metric: Callable[[str], None] | None = None,
    settings: Settings,
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
    6. Detection (impure, via callback)
    7. Pipeline dispatch trigger (impure, via dispatch_for_submission helper)

    Args:
        session: SQLAlchemy async session
        principal: Current authenticated principal
        request: Submission creation request
        existing_slugs: Set of already-used slugs
        parsed_manifest: Pre-validated ManifestV1. If None, validates request.manifest.
        emit_submission_metric: Optional callback to emit submission metric with status.
                                Called with the submission status string.
        settings: Application Settings — required for dispatch (gh_pat, pipeline_kv_uri,
                  callback_base_url, pipeline_timeout_minutes).
        detection_fn: Optional async callable that runs detection rules. Called with
                      (session, submission). When None, detection is skipped.
        validate_pi_fn: Optional async callable that validates the PI OID against
                        Microsoft Graph.  Called with ``request.pi_principal_id``
                        before the GitHub repo check.  When None, PI validation is
                        skipped (used in tests and legacy code paths).

    Returns:
        The created Submission ORM object

    Raises:
        ValidationApiError: If PI is invalid, repository not found, validation fails,
                            or payload is too large for GitHub dispatch.
        DispatchUnavailableError: If gh_pat or pipeline_kv_uri is unset (caller maps to 503).
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

    # Step 2: Resolve manifest — validate if request.manifest is a dict (AC8.6)
    resolved_manifest_json: dict[str, Any] | None = None

    if parsed_manifest is not None:
        # Caller already validated and passed a ManifestV1 — use it directly
        resolved_manifest_json = parsed_manifest.model_dump(mode="json")
    elif isinstance(request.manifest, dict):
        # Validate the dict and reject shared_reference assets (AC8.6)
        try:
            validated = manifest_from_dict(request.manifest)
            reject_shared_references(validated)
        except SharedReferenceNotYetSupportedError as exc:
            raise ValidationApiError(
                code="shared_reference_not_supported",
                public_message=exc.message,
            ) from exc
        resolved_manifest_json = validated.model_dump(mode="json")
    # else: no manifest provided — leave as None for now

    # Step 3: Derive slug (pure)
    slug = derive_slug(request.paper_title, str(request.github_repo_url), existing_slugs)

    # Step 4: Validate GitHub repository (impure, may raise)
    await validate_repo(
        request.github_repo_url,
        request.git_ref,
        request.dockerfile_path,
    )

    # Step 5: Create submission with validated data (impure)
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
        manifest=resolved_manifest_json,
    )

    session.add(submission)
    await session.flush()  # Get the server-generated UUIDv7

    # Step 6: Record approval event for submission creation (impure)
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

    # Step 7: Trigger pipeline dispatch — single path through dispatch_for_submission.
    # The helper mints the per-submission HMAC callback secret in the dedicated
    # pipeline KV, builds the payload, and POSTs repository_dispatch. It raises
    # DispatchUnavailableError if RAC_GH_PAT or RAC_PIPELINE_KV_URI is unset.
    #
    # Guard: only dispatch when submission is still awaiting_scan.  If
    # detection transitioned it to needs_user_action the pipeline must NOT
    # launch until the researcher resolves the findings (Critical 2 fix).
    if submission.status == SubmissionStatus.awaiting_scan:
        try:
            await dispatch_for_submission(
                submission,
                settings=settings,
                triggered_by="submission_created",
            )
        except ValidationApiError:
            # Payload too large — mark submission failed, commit so the
            # pipeline_error state survives the re-raise, and propagate so the
            # route layer returns 422.
            submission.status = SubmissionStatus.pipeline_error
            await session.commit()
            logger.error(
                "pipeline_dispatch_payload_too_large",
                submission_id=str(submission.id),
            )
            raise
        except DispatchUnavailableError:
            # Loud-fail: propagate so the route layer maps to 503.
            # Re-raise BEFORE session.commit() — the submission row is
            # rolled back (AC6.2: no orphan row). The helper raised BEFORE
            # mint_callback_secret was called, so no orphan KV secret either
            # (AC6.3). MUST come before the broad `except Exception` clause
            # below — DispatchUnavailableError(Exception) inherits from
            # Exception, so without this explicit clause the broad-catch
            # would swallow it.
            raise
        except Exception as exc:
            # 5xx / network error — log, leave submission as awaiting_scan.
            # Operator retries via the admin endpoint.
            logger.error(
                "pipeline_dispatch_failed",
                submission_id=str(submission.id),
                error=str(exc),
            )
            # Do NOT re-raise; still return 201 to the user.

    return submission
