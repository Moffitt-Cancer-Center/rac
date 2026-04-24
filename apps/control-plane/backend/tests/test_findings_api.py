"""Integration tests for the Findings API (Task 7).

Verifies:
1. Submitter records accept on a warn finding → decision row inserted with correct actor;
   subsequent GET shows it. (AC4.3)
2. AC4.3: decision, rule_id, rule_version, decision_at, decision_actor_principal_id
   are all persisted correctly.
3. Non-submitter/non-admin → 403.
4. Deciding the last open error finding → submission transitions to awaiting_scan.
5. Invalid decision value → 422.

Additional:
- Property tests for needs_user_action_resolved (monotonic in decisions,
  warn decisions don't block resolution).
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.models import (
    ApprovalEvent,
    DetectionFinding,
    DetectionFindingDecision,
    Submission,
    SubmissionStatus,
)
from rac_control_plane.services.detection.resolution import needs_user_action_resolved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_submission_data(submitter_oid: UUID) -> dict:
    return {
        "slug": f"test-{uuid4().hex[:8]}",
        "status": SubmissionStatus.awaiting_scan,
        "submitter_principal_id": submitter_oid,
        "github_repo_url": "https://github.com/test/repo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "pi_principal_id": uuid4(),
        "dept_fallback": "Engineering",
    }


async def _insert_finding(
    session: AsyncSession,
    submission_id: UUID,
    *,
    rule_id: str = "test/rule",
    severity: str = "warn",
) -> DetectionFinding:
    """Insert a DetectionFinding row directly for testing."""
    finding = DetectionFinding(
        submission_id=submission_id,
        rule_id=rule_id,
        rule_version=1,
        severity=severity,
        title=f"Test {severity} finding",
        detail="Test detail",
    )
    session.add(finding)
    await session.flush()
    return finding


# ---------------------------------------------------------------------------
# Scenario 1: Submitter accepts a warn finding (AC4.3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submitter_records_accept_warn_finding(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """AC4.3: Submitter POSTs accept on warn finding → 201, decision row persisted."""
    submitter_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=submitter_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    # Create submission
    sub = Submission(**_make_submission_data(submitter_oid))
    db_setup.add(sub)
    await db_setup.flush()

    # Insert finding
    finding = await _insert_finding(db_setup, sub.id, severity="warn")
    await db_setup.commit()

    # POST decision
    resp = await client.post(
        f"/submissions/{sub.id}/findings/{finding.id}/decisions",
        json={"decision": "accept", "notes": "Looks intentional"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["decision"] == "accept"
    assert data["decision_notes"] == "Looks intentional"
    assert UUID(data["decision_actor_principal_id"]) == submitter_oid
    assert UUID(data["detection_finding_id"]) == finding.id

    # GET /submissions/{id}/findings shows the decision
    resp2 = await client.get(
        f"/submissions/{sub.id}/findings",
        headers=headers,
    )
    assert resp2.status_code == 200
    findings = resp2.json()
    assert len(findings) == 1
    assert findings[0]["latest_decision"] == "accept"


# ---------------------------------------------------------------------------
# Scenario 2: AC4.3 — all fields persisted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac43_all_fields_persisted(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """AC4.3: rule_id, rule_version, decision, decision_at, decision_actor_principal_id persisted."""
    submitter_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=submitter_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    sub = Submission(**_make_submission_data(submitter_oid))
    db_setup.add(sub)
    await db_setup.flush()

    finding = await _insert_finding(db_setup, sub.id, rule_id="dockerfile/inline_downloads", severity="warn")
    await db_setup.commit()

    resp = await client.post(
        f"/submissions/{sub.id}/findings/{finding.id}/decisions",
        json={"decision": "override"},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()

    # AC4.3: verify all required fields
    assert data["decision"] == "override"
    assert data["decision_actor_principal_id"] == str(submitter_oid)
    assert "created_at" in data  # decision_at equivalent
    assert UUID(data["decision_id"])  # decision persisted with its own ID

    # Verify rule_id and rule_version via GET findings
    resp2 = await client.get(f"/submissions/{sub.id}/findings", headers=headers)
    assert resp2.status_code == 200
    f = resp2.json()[0]
    assert f["rule_id"] == "dockerfile/inline_downloads"
    assert f["rule_version"] == 1
    assert f["decision_actor_principal_id"] == str(submitter_oid)
    assert f["decision_at"] is not None


# ---------------------------------------------------------------------------
# Scenario 3: Non-submitter non-admin → 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_submitter_non_admin_forbidden(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """Non-submitter without admin role → 403 on POST decision."""
    submitter_oid = uuid4()
    other_oid = uuid4()
    other_token = mock_oidc.issue_user_token(oid=other_oid, roles=[])
    headers = {"Authorization": f"Bearer {other_token}"}

    sub = Submission(**_make_submission_data(submitter_oid))
    db_setup.add(sub)
    await db_setup.flush()
    finding = await _insert_finding(db_setup, sub.id, severity="warn")
    await db_setup.commit()

    resp = await client.post(
        f"/submissions/{sub.id}/findings/{finding.id}/decisions",
        json={"decision": "accept"},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_submitter_cannot_list_findings(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """Non-submitter without approver role → 403 on GET findings."""
    submitter_oid = uuid4()
    other_oid = uuid4()
    other_token = mock_oidc.issue_user_token(oid=other_oid, roles=[])
    headers = {"Authorization": f"Bearer {other_token}"}

    sub = Submission(**_make_submission_data(submitter_oid))
    db_setup.add(sub)
    await db_setup.commit()

    resp = await client.get(f"/submissions/{sub.id}/findings", headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Scenario 4: Deciding last error finding → submission → awaiting_scan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deciding_last_error_finding_transitions_to_awaiting_scan(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """Deciding the last open error finding → submission transitions back to awaiting_scan.

    We verify via GET /submissions/{id} which reflects the committed state.
    """
    submitter_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=submitter_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    # Create submission in needs_user_action
    sub = Submission(**{
        **_make_submission_data(submitter_oid),
        "status": SubmissionStatus.needs_user_action,
    })
    db_setup.add(sub)
    await db_setup.flush()

    # One error finding
    finding = await _insert_finding(db_setup, sub.id, severity="error")
    await db_setup.commit()

    # Decide: accept
    resp = await client.post(
        f"/submissions/{sub.id}/findings/{finding.id}/decisions",
        json={"decision": "accept"},
        headers=headers,
    )
    assert resp.status_code == 201

    # Verify submission is back to awaiting_scan via the submissions GET endpoint
    resp2 = await client.get(f"/submissions/{sub.id}", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "awaiting_scan"


# ---------------------------------------------------------------------------
# Scenario 5: Invalid decision value → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_decision_value_returns_422(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """Invalid decision value → 422 Unprocessable Entity."""
    submitter_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=submitter_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    sub = Submission(**_make_submission_data(submitter_oid))
    db_setup.add(sub)
    await db_setup.flush()
    finding = await _insert_finding(db_setup, sub.id, severity="warn")
    await db_setup.commit()

    resp = await client.post(
        f"/submissions/{sub.id}/findings/{finding.id}/decisions",
        json={"decision": "INVALID_DECISION"},
        headers=headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario 6: Dismiss does NOT resolve an error finding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dismiss_does_not_resolve_error_finding(
    client, db_setup: AsyncSession, mock_oidc
) -> None:
    """dismiss on an error finding does NOT transition submission back to awaiting_scan."""
    submitter_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=submitter_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}

    sub = Submission(**{
        **_make_submission_data(submitter_oid),
        "status": SubmissionStatus.needs_user_action,
    })
    db_setup.add(sub)
    await db_setup.flush()

    finding = await _insert_finding(db_setup, sub.id, severity="error")
    await db_setup.commit()

    resp = await client.post(
        f"/submissions/{sub.id}/findings/{finding.id}/decisions",
        json={"decision": "dismiss"},
        headers=headers,
    )
    assert resp.status_code == 201

    # Submission should still be needs_user_action (dismiss doesn't resolve error)
    resp2 = await client.get(f"/submissions/{sub.id}", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "needs_user_action"


# ---------------------------------------------------------------------------
# Property tests for needs_user_action_resolved
# ---------------------------------------------------------------------------

@given(
    n_warn=st.integers(min_value=0, max_value=5),
    n_error_resolved=st.integers(min_value=0, max_value=5),
)
@hyp_settings(max_examples=50)
def test_property_all_errors_resolved_returns_true(
    n_warn: int,
    n_error_resolved: int,
) -> None:
    """Property: all error findings with accept/override/auto_fix → True."""
    findings = [
        {"severity": "warn", "latest_decision": "accept"}
        for _ in range(n_warn)
    ] + [
        {"severity": "error", "latest_decision": "accept"}
        for _ in range(n_error_resolved)
    ]
    assert needs_user_action_resolved(findings) is True


@given(
    n_warn=st.integers(min_value=0, max_value=5),
    n_error_resolved=st.integers(min_value=0, max_value=5),
    n_error_unresolved=st.integers(min_value=1, max_value=5),
)
@hyp_settings(max_examples=50)
def test_property_unresolved_error_returns_false(
    n_warn: int,
    n_error_resolved: int,
    n_error_unresolved: int,
) -> None:
    """Property: any error finding without a resolving decision → False."""
    findings = (
        [{"severity": "warn", "latest_decision": "accept"}] * n_warn
        + [{"severity": "error", "latest_decision": "accept"}] * n_error_resolved
        + [{"severity": "error", "latest_decision": None}] * n_error_unresolved
    )
    assert needs_user_action_resolved(findings) is False


@given(n_warn=st.integers(min_value=0, max_value=10))
@hyp_settings(max_examples=30)
def test_property_warn_decisions_dont_affect_result(n_warn: int) -> None:
    """Property: severity=warn findings with or without decisions don't block resolution."""
    # Only warn findings, all undecided
    findings = [{"severity": "warn", "latest_decision": None} for _ in range(n_warn)]
    # No error findings → should always resolve
    assert needs_user_action_resolved(findings) is True
