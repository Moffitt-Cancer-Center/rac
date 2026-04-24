# pattern: Imperative Shell
"""Ownership transfer service.

Transfers PI ownership of an app to a new Entra principal.  Preserves
the full approval event audit trail (AC9.3): existing approval_event rows
are NEVER modified; only a new 'ownership_transferred' event is appended.

Design note: the transfer comment stores structured JSON in the
ApprovalEvent.comment field rather than a dedicated JSONB column.  This
avoids an additional migration while keeping the audit trail readable.
The shape stored is:
    {"from": "<old_pi_oid>", "to": "<new_pi_oid>", "justification": "..."}

If a 'detail' JSONB column is added to approval_event in a future migration,
the read path should prefer that column.

Verifies: rac-v1.AC9.3
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    App,
    AppOwnershipFlag,
    AppOwnershipFlagReview,
    ApprovalEvent,
)
from rac_control_plane.errors import NotFoundError, ValidationApiError
from rac_control_plane.services.ownership.graph_gateway import get_user
from rac_control_plane.services.ownership.pi_validation import Invalid, Ok, is_valid_pi

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TransferRequest:
    """Inputs required to transfer app ownership to a new PI."""

    app_id: UUID
    new_pi_principal_id: UUID
    new_dept_fallback: str
    justification: str


ValidationResult = Ok | Invalid


async def _default_validate_pi_fn(oid: UUID) -> ValidationResult:
    """Default PI validator: calls the live Graph gateway."""
    user = await get_user(oid)
    return is_valid_pi(user)


async def transfer_ownership(
    session: AsyncSession,
    req: TransferRequest,
    actor_principal_id: UUID,
    *,
    validate_pi_fn: Callable[[UUID], Awaitable[ValidationResult]] | None = None,
) -> App:
    """Transfer ownership of an app to a new PI.

    Steps:
    1. Validate new PI via validate_pi_fn.  Invalid → ValidationApiError (422).
    2. Load app row.  Not found → NotFoundError (404).
    3. Capture old PI and dept.
    4. UPDATE app.pi_principal_id, app.dept_fallback, app.updated_at.
    5. INSERT approval_event(kind='ownership_transferred', comment=JSON).
    6. Resolve open app_ownership_flag rows (reason='account_disabled') by
       inserting app_ownership_flag_review(review_decision='resolved_by_transfer').
    7. Return the updated App (caller commits).

    The caller (route handler) must commit the session after this returns.
    Existing approval_event rows are not touched (AC9.3).

    Args:
        session: Active async session (caller commits).
        req: Transfer parameters.
        actor_principal_id: OID of the admin performing the transfer.
        validate_pi_fn: Injectable PI validation function.  Defaults to
                        the live Graph gateway + is_valid_pi.

    Returns:
        Updated App ORM row.

    Raises:
        ValidationApiError (422): new PI is invalid in Graph.
        NotFoundError (404): app_id not found.
    """
    effective_validate = validate_pi_fn or _default_validate_pi_fn

    # ── Step 1: Validate new PI ───────────────────────────────────────────
    pi_result = await effective_validate(req.new_pi_principal_id)
    if isinstance(pi_result, Invalid):
        raise ValidationApiError(
            code="invalid_new_pi",
            public_message=(
                f"New PI {req.new_pi_principal_id} is not a valid Entra principal: "
                f"{pi_result.reason}"
            ),
        )

    # ── Step 2: Load app ──────────────────────────────────────────────────
    app = await session.get(App, req.app_id)
    if app is None:
        raise NotFoundError(public_message=f"App {req.app_id} not found")

    # ── Step 3: Capture old values ────────────────────────────────────────
    old_pi = app.pi_principal_id
    old_dept = app.dept_fallback

    # ── Step 4: Update app row ────────────────────────────────────────────
    app.pi_principal_id = req.new_pi_principal_id
    app.dept_fallback = req.new_dept_fallback
    app.updated_at = datetime.now(UTC)
    session.add(app)

    # ── Step 5: Append audit event ────────────────────────────────────────
    comment_payload = json.dumps(
        {
            "from": str(old_pi),
            "to": str(req.new_pi_principal_id),
            "old_dept": old_dept,
            "new_dept": req.new_dept_fallback,
            "justification": req.justification,
        }
    )
    # approval_event.submission_id is NOT NULL in older migrations but the model
    # defines it as nullable.  Since this event is app-level (not submission-level),
    # we need to find any submission linked to this app or leave submission_id NULL.
    # The model allows nullable submission_id for exactly this case.
    event = ApprovalEvent(
        submission_id=None,  # app-level event; submission_id is nullable since migration 0007
        kind="ownership_transferred",
        actor_principal_id=actor_principal_id,
        comment=comment_payload,
        decision=None,
        payload=None,
    )
    session.add(event)

    # ── Step 6: Resolve open flags (account_disabled reason) ─────────────
    open_flags_stmt = (
        select(AppOwnershipFlag)
        .outerjoin(
            AppOwnershipFlagReview,
            AppOwnershipFlagReview.flag_id == AppOwnershipFlag.id,
        )
        .where(
            AppOwnershipFlag.app_id == req.app_id,
            AppOwnershipFlag.reason == "account_disabled",
            AppOwnershipFlagReview.id.is_(None),  # no review yet
        )
    )
    open_flags_result = await session.execute(open_flags_stmt)
    open_flags = list(open_flags_result.scalars().all())

    for flag in open_flags:
        review = AppOwnershipFlagReview(
            flag_id=flag.id,
            review_decision="resolved_by_transfer",
            reviewer_principal_id=actor_principal_id,
            notes=req.justification,
        )
        session.add(review)

    logger.info(
        "ownership_transferred",
        app_id=str(req.app_id),
        old_pi=str(old_pi),
        new_pi=str(req.new_pi_principal_id),
        resolved_flags=len(open_flags),
        actor=str(actor_principal_id),
    )

    return app
