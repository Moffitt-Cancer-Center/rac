"""Tests for data/app_repo.py.

Verifies:
- get_by_slug returns None for missing slug.
- upsert_app_for_approved_submission creates a new app row.
- Second upsert for same slug updates current_submission_id atomically.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.app_repo import get_by_slug, upsert_app_for_approved_submission
from rac_control_plane.data.models import App, Submission, SubmissionStatus


async def _make_submission(
    db_setup: AsyncSession,
    *,
    slug: str | None = None,
    status: SubmissionStatus = SubmissionStatus.approved,
) -> Submission:
    slug = slug or f"app-{uuid4().hex[:8]}"
    sub = Submission(
        slug=slug,
        status=status,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=uuid4(),
        dept_fallback="Oncology",
    )
    db_setup.add(sub)
    await db_setup.commit()
    return sub


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_for_missing(db_session: AsyncSession) -> None:
    result = await get_by_slug(db_session, "nonexistent-slug-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_upsert_creates_app_row(db_session: AsyncSession, db_setup: AsyncSession) -> None:
    sub = await _make_submission(db_setup)
    result = await db_session.execute(select(Submission).where(Submission.id == sub.id))
    sub_in = result.scalar_one()

    app = await upsert_app_for_approved_submission(db_session, sub_in)

    assert app is not None
    assert app.slug == sub.slug
    assert app.current_submission_id == sub.id
    assert str(app.pi_principal_id) == str(sub.pi_principal_id)


@pytest.mark.asyncio
async def test_upsert_returns_same_app_on_conflict(
    db_session: AsyncSession,
    db_setup: AsyncSession,
) -> None:
    """Same slug → second upsert updates current_submission_id, returns same app row."""
    slug = f"shared-{uuid4().hex[:8]}"

    sub1 = await _make_submission(db_setup, slug=slug)
    result = await db_session.execute(select(Submission).where(Submission.id == sub1.id))
    sub1_in = result.scalar_one()

    app1 = await upsert_app_for_approved_submission(db_session, sub1_in)
    app_id_first = app1.id

    sub2 = await _make_submission(db_setup, slug=slug)
    result2 = await db_session.execute(select(Submission).where(Submission.id == sub2.id))
    sub2_in = result2.scalar_one()

    app2 = await upsert_app_for_approved_submission(db_session, sub2_in)

    # Same app row
    assert app2.id == app_id_first
    # Updated submission pointer
    assert app2.current_submission_id == sub2.id


@pytest.mark.asyncio
async def test_get_by_slug_finds_created_app(
    db_session: AsyncSession,
    db_setup: AsyncSession,
) -> None:
    sub = await _make_submission(db_setup)
    result = await db_session.execute(select(Submission).where(Submission.id == sub.id))
    sub_in = result.scalar_one()

    await upsert_app_for_approved_submission(db_session, sub_in)

    found = await get_by_slug(db_session, sub.slug)
    assert found is not None
    assert found.slug == sub.slug
