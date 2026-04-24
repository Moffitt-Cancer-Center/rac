"""Integration tests for the nightly Graph sweep.

Uses a real Postgres testcontainer + committable sessions (same pattern as
test_provisioning_orchestrator.py).

Verifies:
- Active PI → no flag inserted.
- Deactivated PI → one flag row, reason='account_disabled'.
- Deleted PI (None from Graph) → one flag row, reason='not_found'.
- Re-run sweep when flag exists AND unreviewed → idempotent (no duplicate).
- Re-run sweep when flag is reviewed → may re-flag if still disabled.
  Current design: sweep skips any PI with an OPEN flag (unreviewed); once a
  flag is reviewed, the PI is no longer in the skip-set so the sweep will
  re-flag if the PI is still disabled.

Verifies: rac-v1.AC9.2
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rac_control_plane.data.models import (
    App,
    AppOwnershipFlag,
    AppOwnershipFlagReview,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.services.ownership.deactivation_logic import GraphUserSnapshot
from rac_control_plane.services.ownership.graph_sweep import SweepResult, run_sweep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sweep_session(migrated_db: str):  # type: ignore[no-untyped-def]
    """Committable session for sweep tests.

    run_sweep does NOT commit internally — the caller (cli/graph_sweep.py or
    test) commits.  We use a committable session so we can commit setup data
    and then commit sweep results and verify them.
    """
    engine = create_async_engine(migrated_db, echo=False, poolclass=NullPool)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_deployed_app(
    session: AsyncSession,
    *,
    pi_oid: UUID | None = None,
) -> App:
    """Insert a deployed app (current_submission_id set) using a committed session."""
    pi = pi_oid or uuid4()
    slug = f"app-{uuid4().hex[:8]}"

    sub = Submission(
        slug=slug,
        status=SubmissionStatus.deployed,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=pi,
        dept_fallback="Test Dept",
    )
    session.add(sub)
    await session.flush()

    app = App(
        slug=slug,
        pi_principal_id=pi,
        dept_fallback="Test Dept",
        current_submission_id=sub.id,
        target_port=8000,
        cpu_cores=0.25,
        memory_gb=0.5,
    )
    session.add(app)
    await session.commit()
    return app


def _active_fn(oid: UUID):  # type: ignore[no-untyped-def]
    """graph_fn that returns an active snapshot for any OID."""
    async def _fn(oids: list[UUID]) -> dict[UUID, GraphUserSnapshot | None]:
        return {o: GraphUserSnapshot(oid=o, account_enabled=True) for o in oids}
    return _fn


def _disabled_for(target_oid: UUID):  # type: ignore[no-untyped-def]
    """graph_fn that returns disabled for target_oid, active for all others.

    This avoids cross-test interference: the sweep sees all apps in the DB
    (from committed prior tests) but only flags the one we care about.
    """
    async def _fn(oids: list[UUID]) -> dict[UUID, GraphUserSnapshot | None]:
        return {
            o: GraphUserSnapshot(oid=o, account_enabled=(o != target_oid))
            for o in oids
        }
    return _fn


def _missing_for(target_oid: UUID):  # type: ignore[no-untyped-def]
    """graph_fn that returns None for target_oid, active for all others."""
    async def _fn(oids: list[UUID]) -> dict[UUID, GraphUserSnapshot | None]:
        return {
            o: (None if o == target_oid else GraphUserSnapshot(oid=o, account_enabled=True))
            for o in oids
        }
    return _fn


def _disabled_fn():  # type: ignore[no-untyped-def]
    """graph_fn that returns a disabled snapshot for ANY OID (use with care in shared DB)."""
    async def _fn(oids: list[UUID]) -> dict[UUID, GraphUserSnapshot | None]:
        return {o: GraphUserSnapshot(oid=o, account_enabled=False) for o in oids}
    return _fn


def _missing_fn():  # type: ignore[no-untyped-def]
    """graph_fn that returns None for any OID (use with care in shared DB)."""
    async def _fn(oids: list[UUID]) -> dict[UUID, GraphUserSnapshot | None]:
        return {o: None for o in oids}
    return _fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_pi_produces_no_flag(sweep_session: AsyncSession) -> None:
    """Active PI: sweep runs cleanly, no flag row inserted."""
    pi = uuid4()
    await _insert_deployed_app(sweep_session, pi_oid=pi)

    result = await run_sweep(sweep_session, graph_fn=_active_fn(pi))
    await sweep_session.commit()

    assert isinstance(result, SweepResult)
    assert result.flagged_count == 0

    # Confirm no flag rows exist for this PI
    flags = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.pi_principal_id == pi)
        )
    ).scalars().all()
    assert len(flags) == 0


@pytest.mark.asyncio
async def test_deactivated_pi_produces_account_disabled_flag(
    sweep_session: AsyncSession,
) -> None:
    """Disabled PI: sweep inserts one flag row with reason='account_disabled'."""
    pi = uuid4()
    app = await _insert_deployed_app(sweep_session, pi_oid=pi)

    # Use targeted fn so other apps in the shared DB are treated as active
    result = await run_sweep(sweep_session, graph_fn=_disabled_for(pi))
    await sweep_session.commit()

    # At least one flag for our app; there may be flags for other apps from prior tests
    # if they happened to already have open flags (they'd be skipped, not re-flagged).
    # We assert the specific flag for our app exists.
    flags = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.app_id == app.id)
        )
    ).scalars().all()
    assert len(flags) == 1
    assert flags[0].reason == "account_disabled"
    assert flags[0].pi_principal_id == pi


@pytest.mark.asyncio
async def test_deleted_pi_produces_not_found_flag(sweep_session: AsyncSession) -> None:
    """PI not found in Graph (None): sweep inserts flag with reason='not_found'."""
    pi = uuid4()
    app = await _insert_deployed_app(sweep_session, pi_oid=pi)

    result = await run_sweep(sweep_session, graph_fn=_missing_for(pi))
    await sweep_session.commit()

    flags = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.app_id == app.id)
        )
    ).scalars().all()
    assert len(flags) == 1
    assert flags[0].reason == "not_found"


@pytest.mark.asyncio
async def test_rerun_with_open_flag_is_idempotent(sweep_session: AsyncSession) -> None:
    """Re-run sweep when flag exists and is unreviewed → no duplicate insert.

    The sweep skips any PI that already has an open (unreviewed) flag.
    This is the primary idempotency guard (append-only design).
    """
    pi = uuid4()
    app = await _insert_deployed_app(sweep_session, pi_oid=pi)

    # First run — inserts the flag
    await run_sweep(sweep_session, graph_fn=_disabled_for(pi))
    await sweep_session.commit()

    flags_after_first = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.app_id == app.id)
        )
    ).scalars().all()
    assert len(flags_after_first) == 1

    # Second run — PI still disabled, but open flag exists → sweep skips this PI
    result2 = await run_sweep(sweep_session, graph_fn=_disabled_for(pi))
    await sweep_session.commit()

    assert result2.flagged_count == 0
    assert result2.skipped_count >= 1

    flags_after_second = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.app_id == app.id)
        )
    ).scalars().all()
    # Still only one flag row — no duplicate
    assert len(flags_after_second) == 1


@pytest.mark.asyncio
async def test_rerun_after_flag_reviewed_may_reflag(sweep_session: AsyncSession) -> None:
    """Re-run after flag is reviewed: PI still disabled → a new flag is inserted.

    Design note: once a flag is reviewed, the PI leaves the 'open flag' skip-set.
    If the PI is still disabled, the sweep will create a new flag on the next run.
    This ensures persistent deactivated-PI problems are not silently ignored after
    an admin acknowledges the first flag.
    """
    pi = uuid4()
    app = await _insert_deployed_app(sweep_session, pi_oid=pi)

    # First run — insert flag
    await run_sweep(sweep_session, graph_fn=_disabled_for(pi))
    await sweep_session.commit()

    # Admin reviews the flag
    flag = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.app_id == app.id)
        )
    ).scalar_one()

    review = AppOwnershipFlagReview(
        flag_id=flag.id,
        review_decision="acknowledged",
        reviewer_principal_id=uuid4(),
        notes="Will follow up",
    )
    sweep_session.add(review)
    await sweep_session.commit()

    # Second run — PI still disabled, flag is now reviewed → sweep re-flags
    result2 = await run_sweep(sweep_session, graph_fn=_disabled_for(pi))
    await sweep_session.commit()

    # A new flag was created for our specific app
    all_flags = (
        await sweep_session.execute(
            select(AppOwnershipFlag).where(AppOwnershipFlag.app_id == app.id)
        )
    ).scalars().all()
    assert len(all_flags) == 2  # original + new (re-flagged after review)


@pytest.mark.asyncio
async def test_sweep_returns_sweep_result_type(sweep_session: AsyncSession) -> None:
    """run_sweep always returns a SweepResult regardless of DB state."""
    result = await run_sweep(sweep_session, graph_fn=_active_fn(uuid4()))
    assert isinstance(result, SweepResult)
    assert result.flagged_count >= 0
    assert result.skipped_count >= 0
