# pattern: Imperative Shell
"""App repository: atomic upsert for the app table.

Implements ON CONFLICT (slug) DO UPDATE so that re-submissions
atomically update app.current_submission_id.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import App, Submission

logger = structlog.get_logger(__name__)


async def get_by_slug(session: AsyncSession, slug: str) -> App | None:
    """Return the App row for a given slug, or None if not found."""
    stmt = select(App).where(App.slug == slug)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_app_for_approved_submission(
    session: AsyncSession,
    submission: Submission,
) -> App:
    """Atomically insert or update the app row for an approved submission.

    If no app with this slug exists, creates a new App row.
    If an app with this slug already exists (re-submission), atomically
    updates current_submission_id to point at the new submission.

    Uses raw SQL INSERT ... ON CONFLICT (slug) DO UPDATE for atomicity.

    Args:
        session: Active async session (caller commits).
        submission: Approved submission whose slug, pi_principal_id, etc.
                    are used to populate the app row.

    Returns:
        The App ORM object (either newly inserted or existing, with updated fields).
    """
    # Use PostgreSQL INSERT ... ON CONFLICT (slug) DO UPDATE
    # to atomically handle both first-deploy and re-deploy cases.
    stmt = text("""
        INSERT INTO app (slug, pi_principal_id, dept_fallback, current_submission_id,
                         target_port, cpu_cores, memory_gb, access_mode,
                         created_at, updated_at)
        VALUES (:slug, :pi_principal_id, :dept_fallback, :submission_id,
                8000, 0.25, 0.5, 'token_required',
                NOW(), NOW())
        ON CONFLICT (slug) DO UPDATE
            SET current_submission_id = EXCLUDED.current_submission_id,
                pi_principal_id       = EXCLUDED.pi_principal_id,
                dept_fallback         = EXCLUDED.dept_fallback,
                updated_at            = NOW()
        RETURNING id
    """)

    result = await session.execute(
        stmt,
        {
            "slug": submission.slug,
            "pi_principal_id": str(submission.pi_principal_id),
            "dept_fallback": submission.dept_fallback,
            "submission_id": str(submission.id),
        },
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError(f"Upsert returned no row for slug {submission.slug!r}")

    app_id: UUID = row[0]

    # Execute a fresh SELECT with populate_existing=True to ensure the ORM object
    # reflects the actual DB state after the ON CONFLICT update.
    # This handles the re-submission case where a stale instance may be in the
    # session identity map.
    app_result = await session.execute(
        select(App).where(App.id == app_id).execution_options(populate_existing=True)
    )
    app = app_result.scalar_one()
    logger.info("app_upserted", slug=submission.slug, app_id=str(app_id))
    return app
