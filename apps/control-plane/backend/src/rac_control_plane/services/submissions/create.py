# pattern: Imperative Shell
"""Submission creation service orchestrating validation and persistence.

Combines pure slug derivation and validation with database writes.
"""


from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.submissions import SubmissionCreateRequest
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import ApprovalEvent, Submission, SubmissionStatus
from rac_control_plane.services.github_validation import validate_repo
from rac_control_plane.services.submissions.slug import derive_slug


async def create_submission(
    session: AsyncSession,
    principal: Principal,
    request: SubmissionCreateRequest,
    existing_slugs: set[str],
    *,
    emit_submission_metric: Callable[[str], None] | None = None,
) -> Submission:
    """Create a new submission with validation and persistence.

    Orchestrates:
    1. Slug derivation (pure)
    2. GitHub repository validation (impure, raises on error)
    3. Database write with approval_event (impure)
    4. Metric emission (impure, via callback)

    Args:
        session: SQLAlchemy async session
        principal: Current authenticated principal
        request: Submission creation request
        existing_slugs: Set of already-used slugs
        emit_submission_metric: Optional callback to emit submission metric with status.
                                Called with the submission status string.

    Returns:
        The created Submission ORM object

    Raises:
        ValidationApiError: If repository not found or validation fails
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

    return submission
