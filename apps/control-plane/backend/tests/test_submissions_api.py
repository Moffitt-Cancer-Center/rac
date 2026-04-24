"""Integration tests for Submission CRUD API.

Verifies:
- AC2.1: Interactive user creates submission
- AC2.3: Unauthenticated request returns 401
- AC2.4: GitHub validation errors surface as 422
- AC2.6: Principal OID is persisted correctly
- AC3.1: Agent submissions have agent_id populated
- AC3.2: Idempotency-Key prevents duplicates (same-key same-body case)
- AC3.5: Disabled agent returns 403

Test isolation note
--------------------
Each test uses a unique user/agent OID so DB queries can be scoped to that OID
and won't be confused by rows created in other tests.  The `db_session` fixture
uses a SAVEPOINT for the test's own writes, but the app's committed writes are
visible to subsequent queries on the same connection.  All DB assertions are
therefore scoped by the test-specific OID.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.agent_repo import AgentRepo
from rac_control_plane.data.models import ApprovalEvent, DetectionFinding, Submission, SubmissionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_body(
    pi_principal_id: UUID | None = None,
    paper_title: str = "A Study of Integration Testing",
) -> dict[str, object]:
    return {
        "github_repo_url": "https://github.com/testowner/testrepo",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "paper_title": paper_title,
        "pi_principal_id": str(pi_principal_id or uuid4()),
        "dept_fallback": "Bioinformatics",
    }


_GITHUB_REPO_URL = "https://api.github.com/repos/testowner/testrepo"
_GITHUB_DOCKERFILE_URL = (
    "https://api.github.com/repos/testowner/testrepo/contents/Dockerfile"
)


def _mock_github_success() -> respx.MockRouter:
    """Start a respx router that makes GitHub return 200 for both validation calls."""
    router = respx.MockRouter(assert_all_called=False)
    router.get(_GITHUB_REPO_URL).mock(
        return_value=Response(
            200, json={"id": 1, "name": "testrepo", "full_name": "testowner/testrepo"}
        )
    )
    router.get(_GITHUB_DOCKERFILE_URL, params={"ref": "main"}).mock(
        return_value=Response(200, json={"name": "Dockerfile", "path": "Dockerfile"})
    )
    return router


# ---------------------------------------------------------------------------
# Test 1 — AC2.1: Interactive user creates a submission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_interactive_user_creates(client, db_session, mock_oidc):
    """AC2.1: Authenticated researcher POSTs valid body → 201, row in DB, approval_event."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}
    pi_id = uuid4()
    body = _valid_body(pi_principal_id=pi_id)

    with _mock_github_success() as mock:
        mock.start()
        response = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response.status_code == 201, response.text
    data = response.json()

    # Response shape
    assert "id" in data
    sub_id = UUID(data["id"])
    assert data["status"] == "awaiting_scan"
    assert len(data["slug"]) > 0
    assert data["submitter_principal_id"] == str(test_user_oid)
    assert data["agent_id"] is None

    # DB: Submission row scoped by the submission ID from the response
    stmt = select(Submission).where(Submission.id == sub_id)
    row = await db_session.scalar(stmt)
    assert row is not None, "Submission row not found in DB"
    assert row.submitter_principal_id == test_user_oid
    assert row.status.value == "awaiting_scan"
    assert row.pi_principal_id == pi_id

    # DB: Exactly one approval_event with kind='submission_created' for this submission
    ae_stmt = select(ApprovalEvent).where(ApprovalEvent.submission_id == sub_id)
    ae_result = await db_session.scalars(ae_stmt)
    events = list(ae_result)
    assert len(events) == 1
    ae = events[0]
    assert ae.kind == "submission_created"
    assert ae.actor_principal_id == test_user_oid


# ---------------------------------------------------------------------------
# Test 2 — AC2.3: No auth returns 401 with WWW-Authenticate header
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_no_auth_returns_401(client, mock_oidc):
    """AC2.3: POST /submissions without Authorization header returns 401."""
    body = _valid_body()
    response = await client.post("/submissions", json=body)

    assert response.status_code == 401, response.text

    # Must set WWW-Authenticate: Bearer
    assert response.headers.get("www-authenticate", "").lower().startswith("bearer"), (
        f"Missing WWW-Authenticate: Bearer header. Headers: {dict(response.headers)}"
    )

    # Response body must have code, message, correlation_id
    data = response.json()
    assert "code" in data, f"Missing 'code' in response: {data}"
    assert "message" in data, f"Missing 'message' in response: {data}"
    assert "correlation_id" in data, f"Missing 'correlation_id' in response: {data}"


# ---------------------------------------------------------------------------
# Test 3 — AC2.4: GitHub 404 returns 422 with code='github_not_found'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_github_not_found_returns_422(client, db_session, mock_oidc):
    """AC2.4: If GitHub repo returns 404, POST /submissions returns 422 before DB write."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}
    body = _valid_body()

    with respx.mock(assert_all_called=False) as mock:
        mock.get(_GITHUB_REPO_URL).mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        response = await client.post("/submissions", json=body, headers=headers)

    assert response.status_code == 422, response.text
    data = response.json()
    assert data.get("code") == "github_not_found", (
        f"Expected code='github_not_found', got: {data}"
    )

    # No submission row for this user (no side effects)
    stmt = select(Submission).where(
        Submission.submitter_principal_id == test_user_oid
    )
    result = await db_session.scalars(stmt)
    rows = list(result)
    assert rows == [], f"Expected no submission rows for {test_user_oid}, found {len(rows)}"


# ---------------------------------------------------------------------------
# Test 4 — AC2.6: Principal OID is consistent across submission + approval_event
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_principal_persisted_across_tables(client, db_session, mock_oidc):
    """AC2.6: submitter_principal_id == actor_principal_id == user OID in all rows."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}
    body = _valid_body()

    with _mock_github_success() as mock:
        mock.start()
        response = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response.status_code == 201, response.text
    data = response.json()
    sub_id = UUID(data["id"])

    # Submission row
    sub_stmt = select(Submission).where(Submission.id == sub_id)
    sub = await db_session.scalar(sub_stmt)
    assert sub is not None
    assert sub.submitter_principal_id == test_user_oid, (
        f"submission.submitter_principal_id={sub.submitter_principal_id} != {test_user_oid}"
    )

    # ApprovalEvent rows: every row for this submission carries the same OID
    ae_stmt = select(ApprovalEvent).where(ApprovalEvent.submission_id == sub_id)
    ae_result = await db_session.scalars(ae_stmt)
    events = list(ae_result)
    assert len(events) >= 1, "Expected at least one approval_event row"
    for event in events:
        assert event.actor_principal_id == test_user_oid, (
            f"approval_event.actor_principal_id={event.actor_principal_id} != {test_user_oid}"
        )


# ---------------------------------------------------------------------------
# Test 5 — AC3.1: Agent flow populates agent_id on the submission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_agent_flow_populates_agent_id(client, db_session, db_setup, mock_oidc):
    """AC3.1: Client-credentials (agent) token → submission.agent_id == agent row id.

    Uses db_setup (a separate committed session) to insert the agent so the app
    can see it via its own sessions.
    """
    test_app_id = str(uuid4())
    sp_uuid = uuid4()

    # Insert agent via db_setup (a separate session) and commit so the app can see it
    repo = AgentRepo(db_setup)
    agent = await repo.create_agent(
        name="Test CI Agent",
        kind="cli",
        entra_app_id=test_app_id,
        service_principal_id=sp_uuid,
        enabled=True,
    )
    await db_setup.commit()  # Explicit commit so the app sees the row immediately
    agent_id = agent.id

    token = mock_oidc.issue_client_credentials_token(app_id=test_app_id, scopes=["submit"])
    headers = {"Authorization": f"Bearer {token}"}
    body = _valid_body()

    with _mock_github_success() as mock:
        mock.start()
        response = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["agent_id"] == str(agent_id), (
        f"Expected agent_id={agent_id}, got {data.get('agent_id')}"
    )
    assert data["submitter_principal_id"] == str(sp_uuid), (
        f"Expected submitter_principal_id={sp_uuid}, got {data.get('submitter_principal_id')}"
    )

    # Verify in DB: query by submission id from response
    sub_id = UUID(data["id"])
    sub_stmt = select(Submission).where(Submission.id == sub_id)
    sub = await db_session.scalar(sub_stmt)
    assert sub is not None
    assert sub.agent_id == agent_id
    assert sub.submitter_principal_id == sp_uuid


# ---------------------------------------------------------------------------
# Test 6 — AC3.2: Same Idempotency-Key + same body → replay (200 + X-Idempotent-Replay: true)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_idempotency_same_key_same_body(client, db_session, mock_oidc):
    """AC3.2: Two POSTs with same key+body produce one DB row; second is a 200 replay."""
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    idempotency_key = str(uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }
    body = _valid_body()

    with _mock_github_success() as mock:
        mock.start()
        response1 = await client.post("/submissions", json=body, headers=headers)
        response2 = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response1.status_code == 201, f"First request failed: {response1.text}"
    data1 = response1.json()

    # Second response must be 200 (replay), same id, replay header set
    assert response2.status_code == 200, (
        f"Replay should return 200, got {response2.status_code}: {response2.text}"
    )
    data2 = response2.json()
    assert data2["id"] == data1["id"], (
        f"Replay must return same submission id. Got {data2['id']} vs {data1['id']}"
    )
    assert response2.headers.get("x-idempotent-replay", "").lower() == "true", (
        f"Missing X-Idempotent-Replay: true header. Headers: {dict(response2.headers)}"
    )

    # Exactly one submission row for this user
    stmt = select(Submission).where(
        Submission.submitter_principal_id == test_user_oid
    )
    result = await db_session.scalars(stmt)
    rows = list(result)
    assert len(rows) == 1, f"Expected 1 submission row, found {len(rows)}"


# ---------------------------------------------------------------------------
# Test 7 — AC3.5: Disabled agent returns 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_disabled_agent_returns_403(client, db_session, db_setup, mock_oidc):
    """AC3.5: A disabled agent's token returns 403; no submission row created.

    Uses db_setup (a separate committed session) to insert the disabled agent
    so the app can see it via its own sessions.
    """
    test_app_id = str(uuid4())
    sp_uuid = uuid4()

    # Insert disabled agent via db_setup and commit so the app can see it
    repo = AgentRepo(db_setup)
    await repo.create_agent(
        name="Disabled Agent",
        kind="cli",
        entra_app_id=test_app_id,
        service_principal_id=sp_uuid,
        enabled=False,
    )
    await db_setup.commit()  # Explicit commit so the app sees the row immediately

    token = mock_oidc.issue_client_credentials_token(app_id=test_app_id, scopes=["submit"])
    headers = {"Authorization": f"Bearer {token}"}
    body = _valid_body()

    with respx.mock(assert_all_called=False):
        response = await client.post("/submissions", json=body, headers=headers)

    assert response.status_code == 403, response.text
    data = response.json()
    assert data.get("code") == "forbidden", (
        f"Expected code='forbidden', got: {data.get('code')}"
    )

    # No submission row for this service principal
    stmt = select(Submission).where(
        Submission.submitter_principal_id == sp_uuid
    )
    result = await db_session.scalars(stmt)
    rows = list(result)
    assert rows == [], f"Expected no submission rows for {sp_uuid}, found {len(rows)}"


# ---------------------------------------------------------------------------
# Test 8 — Critical 1: Agent submission with warn finding → needs_user_action
#           and DetectionFinding row exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_submission_with_warn_finding_transitions_to_needs_user_action(
    client, db_session: AsyncSession, db_setup, mock_oidc
) -> None:
    """Critical 1 (Phase 4 review): detection_fn is wired; agent + warn finding → needs_user_action.

    Strategy: monkeypatch _make_detection_fn in the submissions route to return
    a fake that inserts one warn DetectionFinding and transitions the submission
    to needs_user_action, without performing a real git clone.
    """
    from rac_control_plane.data.models import (
        ApprovalEvent,
        DetectionFinding,
        SubmissionStatus,
    )
    from rac_control_plane.services.submissions.fsm import transition as fsm_transition

    test_app_id = str(uuid4())
    sp_uuid = uuid4()

    # Insert enabled agent via db_setup
    repo = AgentRepo(db_setup)
    await repo.create_agent(
        name="Warn-Finding Agent",
        kind="cli",
        entra_app_id=test_app_id,
        service_principal_id=sp_uuid,
        enabled=True,
    )
    await db_setup.commit()

    token = mock_oidc.issue_client_credentials_token(app_id=test_app_id, scopes=["submit"])
    headers = {"Authorization": f"Bearer {token}"}
    body = _valid_body()

    # Build a fake detection_fn that inserts one warn finding and transitions
    # the submission to needs_user_action (mimics what run_detection does for agents).
    async def _fake_detection_fn(session: AsyncSession, submission: Submission) -> list[DetectionFinding]:
        finding = DetectionFinding(
            submission_id=submission.id,
            rule_id="test/warn_rule",
            rule_version=1,
            severity="warn",
            title="Test warn finding",
            detail="Injected by test fake detection_fn",
        )
        session.add(finding)
        await session.flush()

        # Transition the submission to needs_user_action (agent + warn → blocked)
        from rac_control_plane.services.submissions.fsm import SubmissionStatus as FsmStatus
        new_status = fsm_transition(FsmStatus(submission.status), "detection_needs_user_action")
        submission.status = new_status  # type: ignore[assignment]
        session.add(submission)
        await session.flush()

        approval_evt = ApprovalEvent(
            submission_id=submission.id,
            kind="detection_needs_user_action",
            actor_principal_id=submission.submitter_principal_id,
        )
        session.add(approval_evt)
        await session.flush()
        return [finding]

    with (
        patch(
            "rac_control_plane.api.routes.submissions._make_detection_fn",
            return_value=_fake_detection_fn,
        ),
        _mock_github_success() as github_mock,
    ):
        github_mock.start()
        response = await client.post("/submissions", json=body, headers=headers)
        github_mock.stop()

    assert response.status_code == 201, response.text
    data = response.json()
    sub_id = UUID(data["id"])

    # Submission status must be needs_user_action (detection blocked it)
    assert data["status"] == "needs_user_action", (
        f"Expected needs_user_action but got {data['status']}"
    )

    # Verify in DB
    sub_stmt = select(Submission).where(Submission.id == sub_id)
    sub_row = await db_session.scalar(sub_stmt)
    assert sub_row is not None
    assert sub_row.status == SubmissionStatus.needs_user_action

    # DetectionFinding row exists
    finding_stmt = select(DetectionFinding).where(DetectionFinding.submission_id == sub_id)
    finding_result = await db_session.scalars(finding_stmt)
    findings = list(finding_result)
    assert len(findings) == 1, f"Expected 1 DetectionFinding, got {len(findings)}"
    assert findings[0].rule_id == "test/warn_rule"
    assert findings[0].severity == "warn"


# ---------------------------------------------------------------------------
# Test 9 — AC9.1: Invalid PI (not_found) returns 422 with code='invalid_pi'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_invalid_pi_returns_422(client, db_session, mock_oidc):
    """AC9.1: Submission with unknown PI OID returns 422 and no row created.

    Patches graph_gateway.get_user to return None (user not found in tenant).
    """
    test_user_oid = uuid4()
    token = mock_oidc.issue_user_token(oid=test_user_oid, roles=[])
    headers = {"Authorization": f"Bearer {token}"}
    unknown_pi_id = uuid4()
    body = _valid_body(pi_principal_id=unknown_pi_id)

    async def _return_none(oid, *, client=None):  # type: ignore[misc]
        return None

    with patch(
        "rac_control_plane.services.ownership.graph_gateway.get_user",
        side_effect=_return_none,
    ), _mock_github_success() as mock:
        mock.start()
        response = await client.post("/submissions", json=body, headers=headers)
        mock.stop()

    assert response.status_code == 422, response.text
    data = response.json()
    assert data.get("code") == "invalid_pi", (
        f"Expected code='invalid_pi', got: {data}"
    )
    assert "not_found" in data.get("message", ""), (
        f"Expected 'not_found' in message, got: {data.get('message')}"
    )

    # No submission row created (PI check runs before DB write)
    stmt = select(Submission).where(
        Submission.submitter_principal_id == test_user_oid
    )
    result = await db_session.scalars(stmt)
    rows = list(result)
    assert rows == [], f"Expected no submission rows for {test_user_oid}, found {len(rows)}"
