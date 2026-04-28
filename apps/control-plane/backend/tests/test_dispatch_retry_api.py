"""Integration tests for the pipeline dispatch retry admin API.

POST /admin/submissions/{id}/dispatch/retry — re-dispatches the build-and-scan
pipeline for a submission stuck in awaiting_scan. Used to recover submissions
created while dispatch was unconfigured (no PAT, etc.) or after a transient
GitHub Actions failure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import Submission, SubmissionStatus
from rac_control_plane.services.pipeline_dispatch.retry import DispatchUnavailableError


def _admin_token(mock_oidc) -> str:  # type: ignore[no-untyped-def]
    return mock_oidc.issue_user_token(oid=uuid4(), roles=["it_approver"])


def _user_token(mock_oidc) -> str:  # type: ignore[no-untyped-def]
    return mock_oidc.issue_user_token(oid=uuid4(), roles=[])


async def _insert_submission(
    db_setup: AsyncSession,
    *,
    slug: str | None = None,
    status: SubmissionStatus = SubmissionStatus.awaiting_scan,
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


@pytest.mark.asyncio
async def test_retry_dispatch_happy_path(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Admin POST returns 200 and invokes the dispatch helper."""
    sub = await _insert_submission(db_setup, status=SubmissionStatus.awaiting_scan)
    token = _admin_token(mock_oidc)

    fake_retry = AsyncMock(
        return_value={
            "submission_id": str(sub.id),
            "callback_url": (
                f"https://cp.rac.example.org/api/webhooks/pipeline-callback/{sub.id}"
            ),
            "dispatched_at": "2026-04-28T18:00:00+00:00",
        }
    )

    with patch(
        "rac_control_plane.api.routes.provisioning.retry_dispatch",
        new=fake_retry,
    ):
        resp = await client.post(
            f"/api/admin/submissions/{sub.id}/dispatch/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["submission_id"] == str(sub.id)
    assert data["callback_url"].endswith(f"/api/webhooks/pipeline-callback/{sub.id}")
    assert "dispatched_at" in data
    assert fake_retry.await_count == 1


@pytest.mark.asyncio
async def test_retry_dispatch_not_found(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Unknown submission ID → 404."""
    token = _admin_token(mock_oidc)
    resp = await client.post(
        f"/api/admin/submissions/{uuid4()}/dispatch/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_dispatch_non_admin_403(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Non-admin token → 403."""
    sub = await _insert_submission(db_setup, status=SubmissionStatus.awaiting_scan)
    token = _user_token(mock_oidc)
    resp = await client.post(
        f"/api/admin/submissions/{sub.id}/dispatch/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_retry_dispatch_wrong_state_409(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """Submission already advanced past awaiting_scan → 409.

    Only awaiting_scan is eligible for retry; pipeline_error needs a different
    remediation path (not just "try again").
    """
    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_research_review
    )
    token = _admin_token(mock_oidc)
    resp = await client.post(
        f"/api/admin/submissions/{sub.id}/dispatch/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_retry_dispatch_pipeline_error_409(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """pipeline_error is also rejected — needs different remediation path."""
    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.pipeline_error
    )
    token = _admin_token(mock_oidc)
    resp = await client.post(
        f"/api/admin/submissions/{sub.id}/dispatch/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_retry_dispatch_no_auth_token_503(client, db_setup, mock_oidc) -> None:  # type: ignore[no-untyped-def]
    """If pipeline dispatch is unconfigured (no PAT), surface 503.

    Better to fail loudly than silently no-op, which is what the original
    create-submission path did via pipeline_dispatch_skipped_no_auth_token.
    """
    sub = await _insert_submission(db_setup, status=SubmissionStatus.awaiting_scan)
    token = _admin_token(mock_oidc)

    # Simulate "no PAT": retry_dispatch raises DispatchUnavailableError.
    with patch(
        "rac_control_plane.api.routes.provisioning.retry_dispatch",
        new=AsyncMock(
            side_effect=DispatchUnavailableError(
                "Pipeline dispatch is not configured: RAC_GH_PAT is unset."
            )
        ),
    ):
        resp = await client.post(
            f"/api/admin/submissions/{sub.id}/dispatch/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "service_unavailable"
    assert "RAC_GH_PAT" in body["message"]
