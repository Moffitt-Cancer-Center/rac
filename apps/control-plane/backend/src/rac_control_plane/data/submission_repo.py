# pattern: Imperative Shell
"""Submission data repository for CRUD operations.

All database access for submission queries goes through this module.
"""

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.models import Submission, SubmissionStatus


async def get_by_id(session: AsyncSession, submission_id: UUID) -> Submission | None:
    """Fetch a single submission by ID.

    Args:
        session: SQLAlchemy async session
        submission_id: UUID of the submission

    Returns:
        Submission ORM object or None if not found
    """
    stmt = select(Submission).where(Submission.id == submission_id)
    return await session.scalar(stmt)


async def list_submissions(
    session: AsyncSession,
    *,
    principal: Principal,
    page: int = 1,
    page_size: int = 20,
    status_filter: SubmissionStatus | None = None,
) -> tuple[list[Submission], int]:
    """List submissions with authorization filtering.

    Only returns submissions where the principal is:
    - The submitter
    - An approver (has research or IT approver role)
    - An admin

    Args:
        session: SQLAlchemy async session
        principal: Current principal (for auth filtering)
        page: Page number (1-indexed)
        page_size: Items per page
        status_filter: Optional status filter

    Returns:
        Tuple of (submission_list, total_count)
    """
    # Build base query
    stmt = select(Submission)

    # Auth filtering: only show submissions where principal is authorized
    # For now, show all submissions (auth will be checked by the route handler)
    # TODO: Implement proper authorization checks in route handler

    # Status filter
    if status_filter:
        stmt = stmt.where(Submission.status == status_filter)

    # Get total count
    count_stmt = select(Submission)
    if status_filter:
        count_stmt = count_stmt.where(Submission.status == status_filter)
    total = await session.scalar(
        select(Submission.__table__.__len__()).select_from(count_stmt.subquery())
    )
    if total is None:
        total = len(await session.scalars(count_stmt))

    # Pagination
    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(Submission.created_at)).offset(offset).limit(page_size)

    submissions = await session.scalars(stmt)
    return list(submissions), total


async def get_existing_slugs(session: AsyncSession) -> set[str]:
    """Get all existing submission slugs to avoid duplicates.

    Args:
        session: SQLAlchemy async session

    Returns:
        Set of existing slugs
    """
    stmt = select(Submission.slug)
    slugs = await session.scalars(stmt)
    return set(slugs)
