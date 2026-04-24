"""Integration tests for the provisioning retry API.

Verifies:
- AC6.3: GET /admin/submissions/failed-provisions returns correct rows.
- AC6.3: POST /admin/submissions/{id}/provisioning/retry calls orchestrator.
- 403 for non-admin.
- 409 for submission not in 'approved' state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    ApprovalEvent,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.services.provisioning.orchestrator import ProvisioningOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_token(mock_oidc) -> str:  # type: ignore[no-untyped-def]
    return mock_oidc.issue_user_token(oid=uuid4(), roles=["it_approver"])


def _user_token(mock_oidc) -> str:  # type: ignore[no-untyped-def]
    return mock_oidc.issue_user_token(oid=uuid4(), roles=[])


async def _insert_submission(
    db_setup: AsyncSession,
    *,
    slug: str | None = None,
    status: SubmissionStatus = SubmissionStatus.approved,
) -> Submission:
    slug = slug or f"sub-{uuid4().hex[:8]}"
    sub = Submission(
        slug=slug,
        status=status,
        submitter_principal_id=uuid4(),
        github_repo_url="https://github.com/test/repo",
        git_ref="main",
        dockerfile_path="Dockerfile",
        pi_principal_id=uuid4(),
        dept_fallback="Test Dept",
    )
    db_setup.add(sub)
    await db_setup.commit()
    return sub


async def _insert_approval_event(
    db_setup: AsyncSession,
    submission_id: object,
    kind: str,
    comment: str = "",
) -> None:
    ev = ApprovalEvent(
        submission_id=submission_id,
        kind=kind,
        actor_principal_id=None,
        comment=comment,
    )
    db_setup.add(ev)
    await db_setup.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_provisions_list(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """GET /admin/submissions/failed-provisions returns only failed-and-not-deployed submissions."""
    # 1. Successful submission: approved + provisioning_completed — should NOT appear
    sub_ok = await _insert_submission(db_setup, status=SubmissionStatus.approved)
    await _insert_approval_event(db_setup, sub_ok.id, "provisioning_completed")

    # 2. Failed submission 1
    sub_fail1 = await _insert_submission(db_setup, status=SubmissionStatus.approved)
    await _insert_approval_event(db_setup, sub_fail1.id, "provisioning_failed", "aca_transient: 503")

    # 3. Failed submission 2
    sub_fail2 = await _insert_submission(db_setup, status=SubmissionStatus.approved)
    await _insert_approval_event(db_setup, sub_fail2.id, "provisioning_failed", "dns_conflict: conflict")

    token = _admin_token(mock_oidc)
    resp = await client.get(
        "/admin/submissions/failed-provisions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Only the 2 failed submissions should appear
    ids = {str(row["submission_id"]) for row in data}
    assert str(sub_fail1.id) in ids
    assert str(sub_fail2.id) in ids
    assert str(sub_ok.id) not in ids


@pytest.mark.asyncio
async def test_failed_provisions_shape(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Response rows have the expected fields."""
    sub = await _insert_submission(db_setup, status=SubmissionStatus.approved)
    await _insert_approval_event(db_setup, sub.id, "provisioning_failed", "aca_transient: 503")

    token = _admin_token(mock_oidc)
    resp = await client.get(
        "/admin/submissions/failed-provisions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    matching = [r for r in data if r["submission_id"] == str(sub.id)]
    assert len(matching) == 1
    row = matching[0]
    assert row["slug"] == sub.slug
    assert "pi_principal_id" in row
    assert "last_failure_reason" in row
    assert "failed_at" in row
    assert "retry_count" in row


@pytest.mark.asyncio
async def test_retry_calls_orchestrator(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """POST /admin/submissions/{id}/provisioning/retry calls provision_submission."""
    sub = await _insert_submission(db_setup, status=SubmissionStatus.approved)
    await _insert_approval_event(db_setup, sub.id, "provisioning_failed", "aca_transient: 503")

    success_outcome = ProvisioningOutcome(
        success=True,
        submission_id=sub.id,
        app_id=uuid4(),
    )

    token = _admin_token(mock_oidc)

    with patch(
        "rac_control_plane.api.routes.provisioning.provision_submission",
        new=AsyncMock(return_value=success_outcome),
    ) as mock_provision:
        resp = await client.post(
            f"/admin/submissions/{sub.id}/provisioning/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["submission_id"] == str(sub.id)
    assert mock_provision.called


@pytest.mark.asyncio
async def test_retry_non_admin_403(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Non-admin token → 403."""
    sub = await _insert_submission(db_setup, status=SubmissionStatus.approved)

    token = _user_token(mock_oidc)
    resp = await client.post(
        f"/admin/submissions/{sub.id}/provisioning/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_retry_wrong_state(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Submission in 'awaiting_scan' → 409."""
    sub = await _insert_submission(db_setup, status=SubmissionStatus.awaiting_scan)

    token = _admin_token(mock_oidc)
    resp = await client.post(
        f"/admin/submissions/{sub.id}/provisioning/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_retry_not_found(client, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Unknown submission ID → 404."""
    token = _admin_token(mock_oidc)
    resp = await client.post(
        f"/admin/submissions/{uuid4()}/provisioning/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
