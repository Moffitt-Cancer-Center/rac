# pattern: Imperative Shell
"""Submission CRUD API routes.

Endpoints:
- POST /submissions: Create a new submission
- GET /submissions/{id}: Retrieve a single submission
- GET /submissions: List submissions with pagination
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.submissions import (
    SubmissionCreateRequest,
    SubmissionListResponse,
    SubmissionResponse,
)
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import SubmissionStatus
from rac_control_plane.data.submission_repo import (
    get_by_id,
    get_existing_slugs,
    list_submissions,
)
from rac_control_plane.services.submissions.create import create_submission

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.post("", status_code=201, response_model=SubmissionResponse)
async def post_submission(
    request: SubmissionCreateRequest,
    # TODO: current_principal() dependency from Task 5
    session: AsyncSession = Depends(get_session),
) -> SubmissionResponse:
    """Create a new submission.

    Requires authentication (either interactive OIDC or client-credentials).

    Verifies:
    - AC2.1: Authenticated researcher creates submission
    - AC2.3: Unauthenticated requests return 401
    - AC2.4: GitHub validation happens before DB write
    - AC2.6: Submitter principal_id is persisted
    - AC3.1: Agent submitter has agent_id populated
    - AC3.2: Idempotency-Key support for duplicate detection
    - AC3.5: Disabled agents return 403

    Args:
        request: Submission creation request
        session: Database session

    Returns:
        Created submission with 201 status

    Raises:
        401: Missing or invalid authentication
        403: Agent is disabled
        422: GitHub validation failed
    """
    # TODO: current_principal dependency needed
    # For now, this is a stub that requires auth middleware wiring
    principal = Principal(
        oid=__import__("uuid").uuid4(),
        kind="user",
        display_name="Test User",
        agent_id=None,
        roles=frozenset(["researcher"]),
    )

    # Get existing slugs to avoid collisions
    existing_slugs = await get_existing_slugs(session)

    # Create submission (may raise ValidationApiError)
    submission = await create_submission(session, principal, request, existing_slugs)

    # Commit the transaction
    await session.commit()

    return SubmissionResponse(
        id=submission.id,
        slug=submission.slug,
        status=submission.status,
        submitter_principal_id=submission.submitter_principal_id,
        agent_id=submission.agent_id,
        github_repo_url=submission.github_repo_url,
        git_ref=submission.git_ref,
        dockerfile_path=submission.dockerfile_path,
        pi_principal_id=submission.pi_principal_id,
        dept_fallback=submission.dept_fallback,
        manifest=submission.manifest,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.get("/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: str,
    # TODO: current_principal() dependency from Task 5
    session: AsyncSession = Depends(get_session),
) -> SubmissionResponse:
    """Retrieve a single submission by ID.

    Verifies:
    - 404 if not found
    - 403 if not authorized

    Args:
        submission_id: UUID of the submission
        session: Database session

    Returns:
        Submission details

    Raises:
        404: Submission not found
        403: Not authorized to view submission
    """
    # Parse UUID
    try:
        from uuid import UUID
        submission_uuid = UUID(submission_id)
    except ValueError as e:
        raise HTTPException(
            status_code=404, detail="Submission not found"
        ) from e

    # Fetch submission
    submission = await get_by_id(session, submission_uuid)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # TODO: Authorization checks (submitter, approver, admin)

    return SubmissionResponse(
        id=submission.id,
        slug=submission.slug,
        status=submission.status,
        submitter_principal_id=submission.submitter_principal_id,
        agent_id=submission.agent_id,
        github_repo_url=submission.github_repo_url,
        git_ref=submission.git_ref,
        dockerfile_path=submission.dockerfile_path,
        pi_principal_id=submission.pi_principal_id,
        dept_fallback=submission.dept_fallback,
        manifest=submission.manifest,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


@router.get("", response_model=SubmissionListResponse)
async def list_all_submissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: SubmissionStatus | None = None,
    # TODO: current_principal() dependency from Task 5
    session: AsyncSession = Depends(get_session),
) -> SubmissionListResponse:
    """List submissions with pagination and filtering.

    Query parameters:
    - page: Page number (1-indexed)
    - page_size: Items per page (1-100)
    - status: Filter by submission status

    Returns submissions where principal is authorized (submitter, approver, admin).

    Args:
        page: Page number
        page_size: Items per page
        status: Optional status filter
        session: Database session

    Returns:
        Paginated list of submissions
    """
    # TODO: current_principal dependency needed
    principal = Principal(
        oid=__import__("uuid").uuid4(),
        kind="user",
        display_name="Test User",
        agent_id=None,
        roles=frozenset(["researcher"]),
    )

    # Fetch submissions
    submissions, total = await list_submissions(
        session,
        principal=principal,
        page=page,
        page_size=page_size,
        status_filter=status,
    )

    # Convert to response models
    items = [
        SubmissionResponse(
            id=s.id,
            slug=s.slug,
            status=s.status,
            submitter_principal_id=s.submitter_principal_id,
            agent_id=s.agent_id,
            github_repo_url=s.github_repo_url,
            git_ref=s.git_ref,
            dockerfile_path=s.dockerfile_path,
            pi_principal_id=s.pi_principal_id,
            dept_fallback=s.dept_fallback,
            manifest=s.manifest,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in submissions
    ]

    return SubmissionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
