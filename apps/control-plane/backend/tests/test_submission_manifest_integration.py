"""Tests for submission flow integration with manifest + asset finalize.

Verifies:
- Submission with no manifest, no assets → OK, status awaiting_scan.
- Submission with request.manifest = {shared_reference asset} → ValidationApiError
  with code='shared_reference_not_supported' (AC8.6).
- finalize_submission with all assets ready → dispatch_fn called.
- finalize_submission with one hash_mismatch asset → status needs_user_action,
  approval_event kind='asset_resolution_required'.
- finalize_submission with all pending → no-op, still awaiting_scan.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    ApprovalEvent,
    Asset,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.errors import ValidationApiError
from rac_control_plane.services.submissions.finalize import finalize_submission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_submission(session: AsyncSession, submission_id=None, **kwargs) -> Submission:
    """Build and add a minimal Submission row (caller must flush)."""
    sid = submission_id or uuid4()
    sub = Submission(
        id=sid,
        slug=f"test-{sid.hex[:8]}",
        submitter_principal_id=uuid4(),
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        **kwargs,
    )
    session.add(sub)
    return sub


def _make_asset(
    session: AsyncSession,
    submission_id,
    *,
    status: str = "ready",
    kind: str = "upload",
    name: str | None = None,
) -> Asset:
    """Build and add an Asset row (caller must flush)."""
    asset = Asset(
        submission_id=submission_id,
        name=name or f"asset-{uuid4().hex[:6]}",
        kind=kind,
        mount_path="/mnt/data/file",
        status=status,
    )
    session.add(asset)
    return asset


# ---------------------------------------------------------------------------
# test_submission_no_manifest_no_assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submission_no_manifest_no_assets(db_session: AsyncSession) -> None:
    """Submission with no manifest and no assets → stays awaiting_scan."""
    sub = _make_submission(db_session)
    await db_session.flush()

    result = await finalize_submission(db_session, sub.id)

    assert result == SubmissionStatus.awaiting_scan
    # Reload from DB and verify status unchanged
    fresh = await db_session.get(Submission, sub.id)
    assert fresh is not None
    assert fresh.status == SubmissionStatus.awaiting_scan


# ---------------------------------------------------------------------------
# test_submission_shared_reference_rejected (AC8.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submission_shared_reference_rejected() -> None:
    """request.manifest containing a shared_reference asset → ValidationApiError."""
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from rac_control_plane.auth.principal import Principal
    from rac_control_plane.services.submissions.create import create_submission

    session_mock = AsyncMock()
    # Make session.flush() a no-op
    session_mock.flush = AsyncMock()
    session_mock.add = MagicMock()

    principal = Principal(
        oid=uuid4(),
        kind="user",
        display_name="Test User",
        agent_id=None,
        roles=frozenset(),
    )

    # Build a SubmissionCreateRequest with a shared_reference asset in manifest
    from rac_control_plane.api.schemas.submissions import SubmissionCreateRequest
    from pydantic import HttpUrl

    request = SubmissionCreateRequest(
        paper_title="Shared ref test",
        github_repo_url=HttpUrl("https://github.com/test/repo"),
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=uuid4(),
        dept_fallback="Engineering",
        manifest={
            "version": 1,
            "assets": [
                {
                    "kind": "shared_reference",
                    "name": "hg38-genome",
                    "mount_path": "/mnt/ref/hg38",
                    "catalog_id": "hg38",
                }
            ],
        },
    )

    with pytest.raises(ValidationApiError) as exc_info:
        await create_submission(
            session_mock,
            principal,
            request,
            existing_slugs=set(),
            validate_pi_fn=None,
        )

    assert exc_info.value.code == "shared_reference_not_supported"
    assert "hg38-genome" in exc_info.value.public_message


# ---------------------------------------------------------------------------
# test_finalize_all_assets_ready_dispatches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_all_assets_ready_dispatches(db_session: AsyncSession) -> None:
    """finalize_submission with all assets ready → dispatch_fn is called."""
    sub = _make_submission(db_session)
    await db_session.flush()

    _make_asset(db_session, sub.id, status="ready")
    _make_asset(db_session, sub.id, status="ready")
    await db_session.flush()

    dispatched: list[dict] = []

    async def mock_dispatch(payload: dict) -> None:
        dispatched.append(payload)

    result = await finalize_submission(db_session, sub.id, dispatch_fn=mock_dispatch)

    assert result == SubmissionStatus.awaiting_scan
    # dispatch_fn should have been called once
    assert len(dispatched) == 1


# ---------------------------------------------------------------------------
# test_finalize_hash_mismatch_needs_user_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_hash_mismatch_needs_user_action(db_session: AsyncSession) -> None:
    """finalize_submission with one hash_mismatch asset → needs_user_action + approval_event."""
    sub = _make_submission(db_session)
    await db_session.flush()

    # One ready asset, one hash_mismatch asset
    _make_asset(db_session, sub.id, status="ready")
    bad_asset = _make_asset(
        db_session,
        sub.id,
        status="hash_mismatch",
        kind="external_url",
        name="external-data",
    )
    bad_asset.expected_sha256 = "a" * 64
    bad_asset.actual_sha256 = "b" * 64
    await db_session.flush()

    dispatched: list[dict] = []

    async def mock_dispatch(payload: dict) -> None:
        dispatched.append(payload)

    result = await finalize_submission(db_session, sub.id, dispatch_fn=mock_dispatch)

    assert result == SubmissionStatus.needs_user_action

    # Reload submission to confirm DB state changed
    await db_session.refresh(sub)
    assert sub.status == SubmissionStatus.needs_user_action

    # dispatch_fn should NOT have been called (submission blocked)
    assert len(dispatched) == 0

    # Verify approval_event was inserted
    ev_result = await db_session.execute(
        select(ApprovalEvent).where(
            ApprovalEvent.submission_id == sub.id,
            ApprovalEvent.kind == "asset_resolution_required",
        )
    )
    event = ev_result.scalar_one_or_none()
    assert event is not None
    assert event.payload is not None
    blocking = event.payload.get("blocking_assets", [])
    assert any(a["asset_name"] == "external-data" for a in blocking)


# ---------------------------------------------------------------------------
# test_finalize_unreachable_needs_user_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_unreachable_needs_user_action(db_session: AsyncSession) -> None:
    """finalize_submission with an unreachable asset → needs_user_action."""
    sub = _make_submission(db_session)
    await db_session.flush()

    _make_asset(db_session, sub.id, status="unreachable", kind="external_url")
    await db_session.flush()

    result = await finalize_submission(db_session, sub.id)

    assert result == SubmissionStatus.needs_user_action


# ---------------------------------------------------------------------------
# test_finalize_pending_assets_no_op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_pending_assets_no_op(db_session: AsyncSession) -> None:
    """finalize_submission with pending assets → no-op, status stays awaiting_scan."""
    sub = _make_submission(db_session)
    await db_session.flush()

    _make_asset(db_session, sub.id, status="ready")
    _make_asset(db_session, sub.id, status="pending")  # still uploading
    await db_session.flush()

    dispatched: list[dict] = []

    async def mock_dispatch(payload: dict) -> None:
        dispatched.append(payload)

    result = await finalize_submission(db_session, sub.id, dispatch_fn=mock_dispatch)

    assert result == SubmissionStatus.awaiting_scan
    # dispatch_fn must NOT have been called
    assert len(dispatched) == 0


# ---------------------------------------------------------------------------
# test_finalize_skips_non_awaiting_scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_skips_non_awaiting_scan(db_session: AsyncSession) -> None:
    """finalize_submission is a no-op when submission is not awaiting_scan."""
    sub = _make_submission(db_session, status=SubmissionStatus.needs_user_action)
    await db_session.flush()

    # All assets ready — but submission is already in needs_user_action
    _make_asset(db_session, sub.id, status="ready")
    await db_session.flush()

    dispatched: list[dict] = []

    async def mock_dispatch(payload: dict) -> None:
        dispatched.append(payload)

    result = await finalize_submission(db_session, sub.id, dispatch_fn=mock_dispatch)

    # Should return the current status without transitioning
    assert result == SubmissionStatus.needs_user_action
    assert len(dispatched) == 0
