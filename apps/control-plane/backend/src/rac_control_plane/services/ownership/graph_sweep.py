# pattern: Imperative Shell
"""Nightly Graph sweep: detects apps whose PI is deactivated or absent.

Queries deployed apps, batch-looks up each unique PI in Microsoft Graph,
runs the pure compute_flagged_apps decision logic, then appends
app_ownership_flag rows for each newly detected problem.

Append-only: never updates existing flag rows.

Verifies: rac-v1.AC9.2
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import App, AppOwnershipFlag, AppOwnershipFlagReview
from rac_control_plane.services.ownership.deactivation_logic import (
    AppOwnership,
    FlaggedApp,
    GraphUserSnapshot,
    compute_flagged_apps,
)
from rac_control_plane.services.ownership.graph_gateway import (
    GraphUser,
    get_users_batch,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """Summary of a completed sweep run."""

    flagged_count: int
    skipped_count: int


def _to_snapshot(user: GraphUser | None) -> GraphUserSnapshot | None:
    """Convert a GraphUser (or None) to the pure-logic GraphUserSnapshot."""
    if user is None:
        return None
    return GraphUserSnapshot(oid=user.oid, account_enabled=user.account_enabled)


async def _default_graph_fn(
    oids: list[UUID],
) -> dict[UUID, GraphUserSnapshot | None]:
    """Default graph_fn: calls get_users_batch and converts to snapshots."""
    raw = await get_users_batch(oids)
    return {oid: _to_snapshot(user) for oid, user in raw.items()}


async def run_sweep(
    session: AsyncSession,
    *,
    graph_fn: Callable[[list[UUID]], Awaitable[dict[UUID, GraphUserSnapshot | None]]] | None = None,
) -> SweepResult:
    """Run a single sweep pass.

    Steps:
    1. Load all deployed apps (current_submission_id IS NOT NULL).
    2. Collect unique pi_principal_ids.
    3. Skip PIs that already have an OPEN flag (flag exists AND no matching review row).
    4. Batch-look up remaining PIs via Graph.
    5. Run pure compute_flagged_apps.
    6. Insert app_ownership_flag rows for each new flag.
    7. Return SweepResult.

    Args:
        session: Active async session (caller commits after run_sweep returns).
        graph_fn: Injectable Graph lookup function.  Defaults to the real
                  get_users_batch wrapped in snapshot conversion.  Tests
                  inject a synchronous-friendly coroutine factory here.

    Returns:
        SweepResult with counts of newly flagged apps and skipped PIs.
    """
    effective_graph_fn = graph_fn or _default_graph_fn

    # ── Step 1: Load deployed apps ─────────────────────────────────────────
    stmt = select(App).where(App.current_submission_id.is_not(None))
    result = await session.execute(stmt)
    all_apps = list(result.scalars().all())

    if not all_apps:
        logger.info("graph_sweep_no_apps")
        return SweepResult(flagged_count=0, skipped_count=0)

    # ── Step 2: Collect unique PI OIDs ────────────────────────────────────
    all_pi_oids: set[UUID] = {app.pi_principal_id for app in all_apps}

    # ── Step 3: Find PIs with an open (unreviewed) flag ───────────────────
    # An open flag: app_ownership_flag row exists with no app_ownership_flag_review.
    open_flag_stmt = (
        select(AppOwnershipFlag.pi_principal_id)
        .outerjoin(
            AppOwnershipFlagReview,
            AppOwnershipFlagReview.flag_id == AppOwnershipFlag.id,
        )
        .where(AppOwnershipFlagReview.id.is_(None))
        .distinct()
    )
    open_flag_result = await session.execute(open_flag_stmt)
    pis_with_open_flag: set[UUID] = set(open_flag_result.scalars().all())

    # ── Step 4: Determine which PIs to look up ────────────────────────────
    pis_to_check = all_pi_oids - pis_with_open_flag
    skipped_count = len(all_pi_oids) - len(pis_to_check)

    if not pis_to_check:
        logger.info("graph_sweep_all_pis_already_flagged", skipped=skipped_count)
        return SweepResult(flagged_count=0, skipped_count=skipped_count)

    # Build AppOwnership list for only apps whose PI is being checked
    app_ownerships = [
        AppOwnership(
            app_id=app.id,
            app_slug=app.slug,
            pi_principal_id=app.pi_principal_id,
        )
        for app in all_apps
        if app.pi_principal_id in pis_to_check
    ]

    # ── Step 5: Graph batch lookup ────────────────────────────────────────
    graph_results = await effective_graph_fn(list(pis_to_check))

    # ── Step 6: Pure logic ────────────────────────────────────────────────
    flagged: list[FlaggedApp] = compute_flagged_apps(app_ownerships, graph_results)

    # ── Step 7: Insert flag rows ──────────────────────────────────────────
    for flag in flagged:
        row = AppOwnershipFlag(
            app_id=flag.app_id,
            pi_principal_id=flag.pi_principal_id,
            reason=flag.reason,
        )
        session.add(row)

    logger.info(
        "graph_sweep_complete",
        checked_pis=len(pis_to_check),
        skipped_pis=skipped_count,
        flagged_apps=len(flagged),
    )

    return SweepResult(flagged_count=len(flagged), skipped_count=skipped_count)
