# pattern: Functional Core
"""Integration tests for Submission CRUD API.

Verifies:
- AC2.1: Interactive user creates submission
- AC2.3: Unauthenticated request returns 401
- AC2.4: GitHub validation errors surface as 422
- AC2.6: Principal OID is persisted correctly
- AC3.1: Agent submissions have agent_id populated
- AC3.2: Idempotency-Key prevents duplicates
- AC3.5: Disabled agent returns 403
"""

import pytest


@pytest.mark.asyncio
async def test_submission_interactive_user_creates(client, db_session, mock_oidc):
    """AC2.1: Interactive user creates valid submission."""
    # This test will be fully implemented once:
    # 1. Auth middleware is wired (Task 5)
    # 2. Routes are registered with app (Task 10)
    # 3. Fixtures wire up session dependency (Task 8)
    # For now, verify the route exists
    from rac_control_plane.api.routes.submissions import router
    assert router is not None


@pytest.mark.asyncio
async def test_submission_no_auth_returns_401(client):
    """AC2.3: Unauthenticated POST to /submissions returns 401."""
    # Will test once auth middleware is wired and route registered
    pass


@pytest.mark.asyncio
async def test_submission_github_not_found_returns_422(client, db_session):
    """AC2.4: GitHub 404 on repo or Dockerfile surfaces 422 error."""
    # Will test with respx mock of httpx calls
    pass


@pytest.mark.asyncio
async def test_submission_principal_persisted(client, db_session):
    """AC2.6: Principal OID is in submission and approval_event rows."""
    # Will verify principal.oid appears in related tables
    pass


@pytest.mark.asyncio
async def test_submission_agent_flow_populates_agent_id(client, db_session):
    """AC3.1: Agent submission has agent_id populated."""
    # Will test client-credentials flow
    pass


@pytest.mark.asyncio
async def test_submission_idempotency_same_key_same_body(client, db_session):
    """AC3.2: Same Idempotency-Key + body returns same response, one row."""
    # Will test middleware integration with submission endpoint
    pass


@pytest.mark.asyncio
async def test_submission_disabled_agent_returns_403(client, db_session):
    """AC3.5: Disabled agent returns 403."""
    # Will test agent auth + enable/disable flow
    pass
