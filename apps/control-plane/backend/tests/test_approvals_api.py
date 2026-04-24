"""Integration tests for the approval API endpoints.

Verifies:
- AC2.2: Research and IT approval transitions.
- AC10.2: Approval duration histogram emitted.
- 403 for missing roles, 409 for wrong submission state.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import ApprovalEvent, Submission, SubmissionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _research_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=["research_approver"])


def _it_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=["it_approver"])


def _both_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(
        oid=oid, roles=["research_approver", "it_approver"]
    )


def _no_role_token(mock_oidc, oid: UUID) -> str:
    return mock_oidc.issue_user_token(oid=oid, roles=[])


async def _insert_submission(
    db_setup: AsyncSession,
    *,
    status: SubmissionStatus = SubmissionStatus.awaiting_research_review,
) -> Submission:
    """Insert a submission in the given state using a committed setup session."""
    sub = Submission(
        slug=f"test-{uuid4().hex[:8]}",
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


# ---------------------------------------------------------------------------
# Test 1: research approve happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_approve_happy_path(client, db_session, db_setup, mock_oidc) -> None:
    """AC2.2: Research approver + awaiting_research_review → 200, awaiting_it_review."""
    approver_oid = uuid4()
    token = _research_token(mock_oidc, approver_oid)
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_research_review
    )
    sub_id = sub.id

    # Set up an in-memory metric reader to capture the histogram emission.
    # We patch the histogram in the record module directly so that the route
    # handler (which imports it at call time) sees our test instrument.
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    test_meter = provider.get_meter("rac.control_plane")
    test_histogram = test_meter.create_histogram(
        name="rac.approvals.time_to_decision_seconds",
        unit="s",
    )

    recorded_calls: list[tuple[float, dict[str, str]]] = []

    def _capture_record(amount: float, attributes: dict[str, str]) -> None:
        recorded_calls.append((amount, attributes))
        test_histogram.record(amount, attributes)

    with patch(
        "rac_control_plane.services.approvals.record.approval_duration_histogram"
    ) as mock_histogram:
        mock_histogram.record.side_effect = _capture_record

        response = await client.post(
            f"/submissions/{sub_id}/approvals/research",
            json={"decision": "approve"},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "awaiting_it_review", data

    # Verify approval_event row
    ae_stmt = select(ApprovalEvent).where(
        ApprovalEvent.submission_id == sub_id,
        ApprovalEvent.kind == "research_decision",
    )
    events = list(await db_session.scalars(ae_stmt))
    assert len(events) == 1, f"Expected 1 approval_event row, got {len(events)}"
    assert events[0].decision == "approve"
    assert events[0].actor_principal_id == approver_oid

    # Verify histogram was emitted once with correct attributes
    assert len(recorded_calls) == 1, (
        f"Expected histogram.record() called once, got {len(recorded_calls)}"
    )
    _elapsed, attrs = recorded_calls[0]
    assert attrs.get("decision") == "approve"
    assert attrs.get("stage") == "research"
    assert _elapsed >= 0.0


# ---------------------------------------------------------------------------
# Test 2: research approve without role → 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_approve_without_role(client, db_setup, mock_oidc) -> None:
    """403 when principal lacks research_approver role."""
    token = _no_role_token(mock_oidc, uuid4())
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_research_review
    )

    response = await client.post(
        f"/submissions/{sub.id}/approvals/research",
        json={"decision": "approve"},
        headers=headers,
    )

    assert response.status_code == 403, response.text
    data = response.json()
    assert data["code"] == "forbidden"


# ---------------------------------------------------------------------------
# Test 3: research approve wrong state → 409
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_approve_wrong_state(client, db_setup, mock_oidc) -> None:
    """409 when submission is not in awaiting_research_review (e.g. awaiting_scan)."""
    approver_oid = uuid4()
    token = _research_token(mock_oidc, approver_oid)
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_scan
    )

    response = await client.post(
        f"/submissions/{sub.id}/approvals/research",
        json={"decision": "approve"},
        headers=headers,
    )

    assert response.status_code == 409, response.text
    data = response.json()
    assert data["code"] == "conflict"


# ---------------------------------------------------------------------------
# Test 4: IT reject → it_rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_reject(client, db_session, db_setup, mock_oidc) -> None:
    """IT approver + awaiting_it_review + reject → it_rejected."""
    it_approver_oid = uuid4()
    token = _it_token(mock_oidc, it_approver_oid)
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_it_review
    )
    sub_id = sub.id

    response = await client.post(
        f"/submissions/{sub_id}/approvals/it",
        json={"decision": "reject", "notes": "Violates policy"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "it_rejected", data

    # Verify approval_event
    ae_stmt = select(ApprovalEvent).where(
        ApprovalEvent.submission_id == sub_id,
        ApprovalEvent.kind == "it_decision",
    )
    events = list(await db_session.scalars(ae_stmt))
    assert len(events) == 1
    assert events[0].decision == "reject"
    assert events[0].comment == "Violates policy"


# ---------------------------------------------------------------------------
# Test 5: request_changes → needs_assistance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_changes(client, db_session, db_setup, mock_oidc) -> None:
    """request_changes from awaiting_research_review → needs_assistance."""
    approver_oid = uuid4()
    token = _research_token(mock_oidc, approver_oid)
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_research_review
    )
    sub_id = sub.id

    response = await client.post(
        f"/submissions/{sub_id}/approvals/research",
        json={"decision": "request_changes", "notes": "Please add license."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "needs_assistance", data

    # approval_event row exists
    ae_stmt = select(ApprovalEvent).where(
        ApprovalEvent.submission_id == sub_id,
        ApprovalEvent.kind == "research_decision",
    )
    events = list(await db_session.scalars(ae_stmt))
    assert len(events) == 1
    assert events[0].decision == "request_changes"


# ---------------------------------------------------------------------------
# Test 6: IT approve enqueues provisioning stub
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_approve_enqueues_provisioning(client, db_setup, mock_oidc) -> None:
    """IT approve + awaiting_it_review → approved, provisioning stub called."""
    it_approver_oid = uuid4()
    token = _it_token(mock_oidc, it_approver_oid)
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_it_review
    )
    sub_id = sub.id

    provisioning_calls: list[UUID] = []

    async def _mock_enqueue(submission_id: UUID) -> None:
        provisioning_calls.append(submission_id)

    with patch(
        "rac_control_plane.services.approvals.record._enqueue_provisioning_stub",
        side_effect=_mock_enqueue,
    ):
        response = await client.post(
            f"/submissions/{sub_id}/approvals/it",
            json={"decision": "approve"},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "approved", data

    assert len(provisioning_calls) == 1, (
        f"Expected provisioning stub to be called once, got {provisioning_calls}"
    )
    assert provisioning_calls[0] == sub_id


# ---------------------------------------------------------------------------
# Test 7: Full lifecycle — scan → research approve → IT approve → approved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_approval_lifecycle(client, db_session, db_setup, mock_oidc) -> None:
    """End-to-end: scan passed → research approve → IT approve → approved."""
    from rac_control_plane.data.models import SubmissionStatus

    research_oid = uuid4()
    it_oid = uuid4()
    research_token = _research_token(mock_oidc, research_oid)
    it_token = _it_token(mock_oidc, it_oid)

    # Start at awaiting_research_review (scan already passed)
    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_research_review
    )
    sub_id = sub.id

    # Step 1: Research approves
    r1 = await client.post(
        f"/submissions/{sub_id}/approvals/research",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {research_token}"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "awaiting_it_review"

    # Step 2: IT approves
    with patch(
        "rac_control_plane.services.approvals.record._enqueue_provisioning_stub",
        new_callable=AsyncMock,
    ):
        r2 = await client.post(
            f"/submissions/{sub_id}/approvals/it",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {it_token}"},
        )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "approved"

    # Verify two approval_event rows
    ae_stmt = select(ApprovalEvent).where(
        ApprovalEvent.submission_id == sub_id,
    )
    events = sorted(
        list(await db_session.scalars(ae_stmt)),
        key=lambda e: e.created_at,
    )
    decision_events = [e for e in events if e.kind in ("research_decision", "it_decision")]
    assert len(decision_events) == 2

    kinds = {e.kind for e in decision_events}
    assert "research_decision" in kinds
    assert "it_decision" in kinds


# ---------------------------------------------------------------------------
# Test 8: IT approval without IT role → 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_approve_without_it_role(client, db_setup, mock_oidc) -> None:
    """403 when principal has only research role and tries to IT-approve."""
    research_oid = uuid4()
    token = _research_token(mock_oidc, research_oid)  # research role only
    headers = {"Authorization": f"Bearer {token}"}

    sub = await _insert_submission(
        db_setup, status=SubmissionStatus.awaiting_it_review
    )

    response = await client.post(
        f"/submissions/{sub.id}/approvals/it",
        json={"decision": "approve"},
        headers=headers,
    )

    assert response.status_code == 403, response.text
