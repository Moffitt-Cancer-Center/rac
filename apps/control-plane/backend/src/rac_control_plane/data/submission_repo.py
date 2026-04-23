# pattern: Imperative Shell
"""Submission data repository for CRUD operations.

All database access for submission queries goes through this module.
"""

from uuid import UUID

from sqlalchemy import desc, func, select
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
    stmt = select(Submission)
    count_stmt = select(func.count()).select_from(Submission)

    if status_filter:
        stmt = stmt.where(Submission.status == status_filter)
        count_stmt = count_stmt.where(Submission.status == status_filter)

    total = await session.scalar(count_stmt) or 0

    offset = (page - 1) * page_size
    stmt = stmt.order_by(desc(Submission.created_at)).offset(offset).limit(page_size)

    result = await session.scalars(stmt)
    return list(result), total


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
