# pattern: Imperative Shell
"""Submission CRUD API routes.

Endpoints:
- POST /submissions: Create a new submission
- GET /submissions/{id}: Retrieve a single submission
- GET /submissions: List submissions with pagination
"""

import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.submissions import (
    SubmissionCreateRequest,
    SubmissionListResponse,
    SubmissionResponse,
)
from rac_control_plane.auth.dependencies import current_principal
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import DetectionFinding, Submission, SubmissionStatus
from rac_control_plane.data.submission_repo import (
    get_by_id,
    get_existing_slugs,
    list_submissions,
)
from rac_control_plane.errors import ForbiddenError
from rac_control_plane.metrics import submission_counter
from rac_control_plane.services.submissions.create import create_submission
from rac_control_plane.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _make_detection_fn(
    principal_kind: str,
    rules: dict[str, Any] | None = None,
) -> Any:
    """Build a detection_fn closure for use in create_submission.

    Returns an async callable (session, submission) → list[DetectionFinding].

    Args:
        principal_kind: 'user' or 'agent' — drives AC4.5 FSM logic.
        rules: Pre-loaded rules dict (from app.state.rules). If None,
               load_rules() is called lazily inside the closure.
    """
    from rac_control_plane.detection.engine import run_detection

    async def _do_detection(
        session: AsyncSession, submission: Submission
    ) -> list[DetectionFinding]:
        from rac_control_plane.detection.discovery import load_rules as _load_rules

        effective_rules = rules
        if effective_rules is None:
            effective_rules = _load_rules()

        with tempfile.TemporaryDirectory(prefix="rac_detection_") as tmpdir:
            return await run_detection(
                session,
                submission,
                workdir=Path(tmpdir),
                rules=effective_rules,
                principal_kind=principal_kind,
            )

    return _do_detection


def _build_dispatch_fn(
    settings_snapshot: Any,
) -> Any:
    """Build a dispatch callable from current settings.

    Returns an async callable (submission_id, payload) → None, or None
    if dispatch is not configured (no PAT, no App credentials).
    """
    from rac_control_plane.services.pipeline_dispatch import github as gh_dispatch

    auth_token: str | None = None
    if settings_snapshot.gh_pat:
        auth_token = settings_snapshot.gh_pat.get_secret_value()
    # GitHub App auth would be resolved here when implemented (Phase 5+)

    if not auth_token:
        logger.warning("pipeline_dispatch_skipped_no_auth_token")
        return None

    owner = settings_snapshot.gh_pipeline_owner
    repo = settings_snapshot.gh_pipeline_repo

    async def _do_dispatch(payload: dict[str, Any]) -> None:
        await gh_dispatch.dispatch(owner, repo, payload, auth_token=auth_token)

    return _do_dispatch


@router.post("", status_code=201, response_model=SubmissionResponse)
async def post_submission(
    request: SubmissionCreateRequest,
    principal: Annotated[Principal, Depends(current_principal)],
    background_tasks: BackgroundTasks,
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
    - AC5.1: Submission triggers pipeline dispatch after successful DB write

    Args:
        request: Submission creation request
        principal: Current authenticated principal
        background_tasks: FastAPI BackgroundTasks for non-blocking dispatch
        session: Database session

    Returns:
        Created submission with 201 status

    Raises:
        401: Missing or invalid authentication
        403: Agent is disabled
        422: GitHub validation failed or pipeline payload too large
    """
    settings = get_settings()

    # Build dispatch function from settings (None if not configured)
    dispatch_fn = _build_dispatch_fn(settings)

    # Build detection function — source rules from app.state.rules if populated,
    # else let _make_detection_fn call load_rules() lazily inside the closure.
    # Use a local import to avoid a circular import at module level.
    import rac_control_plane.main as _main_mod  # noqa: PLC0415
    _app_state = getattr(getattr(_main_mod, "app", None), "state", None)
    _cached_rules: dict[str, Any] | None = (
        getattr(_app_state, "rules", None) if _app_state else None
    )

    detection_fn = _make_detection_fn(
        principal_kind=principal.kind,
        rules=_cached_rules,
    )

    # Get existing slugs to avoid collisions
    existing_slugs = await get_existing_slugs(session)

    # Build PI validation function closure
    from rac_control_plane.services.ownership import graph_gateway, pi_validation

    async def _validate_pi(oid: Any) -> Any:
        user = await graph_gateway.get_user(oid)
        return pi_validation.is_valid_pi(user)

    # Create submission (may raise ValidationApiError)
    # Pass metric callback to emit submission counter with the target status
    submission = await create_submission(
        session,
        principal,
        request,
        existing_slugs,
        emit_submission_metric=lambda status: submission_counter.add(1, {"status": status}),
        dispatch_fn=dispatch_fn,
        detection_fn=detection_fn,
        validate_pi_fn=_validate_pi,
    )

    # Commit the transaction
    await session.commit()
    # Refresh server-generated fields (updated_at via onupdate, server_default id).
    # Needed when detection_fn has issued an UPDATE that expires server-side columns.
    await session.refresh(submission)

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
    principal: Annotated[Principal, Depends(current_principal)],
    session: AsyncSession = Depends(get_session),
) -> SubmissionResponse:
    """Retrieve a single submission by ID.

    Verifies:
    - 404 if not found
    - 403 if not authorized

    Args:
        submission_id: UUID of the submission
        principal: Current authenticated principal
        session: Database session

    Returns:
        Submission details

    Raises:
        404: Submission not found
        403: Not authorized to view submission
    """
    # Parse UUID
    try:
        submission_uuid = UUID(submission_id)
    except ValueError as e:
        raise HTTPException(
            status_code=404, detail="Submission not found"
        ) from e

    # Fetch submission
    submission = await get_by_id(session, submission_uuid)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Authorization checks: submitter, approver, or admin
    settings = get_settings()
    is_submitter = submission.submitter_principal_id == principal.oid
    is_approver = (
        settings.approver_role_research in principal.roles
        or settings.approver_role_it in principal.roles
    )
    if not is_submitter and not is_approver:
        raise ForbiddenError(public_message="Not authorized to view this submission.")

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
    principal: Annotated[Principal, Depends(current_principal)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: SubmissionStatus | None = None,
    session: AsyncSession = Depends(get_session),
) -> SubmissionListResponse:
    """List submissions with pagination and filtering.

    Returns all submissions (authorization filter is a TODO for v1 per M2 note).

    Query parameters:
    - page: Page number (1-indexed)
    - page_size: Items per page (1-100)
    - status: Filter by submission status

    Args:
        principal: Current authenticated principal
        page: Page number
        page_size: Items per page
        status: Optional status filter
        session: Database session

    Returns:
        Paginated list of submissions
    """
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
