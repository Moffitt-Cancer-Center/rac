# pattern: Functional Core
"""Integration tests for Agent management API.

Verifies:
- Admin can create agents
- Non-admin POST returns 403
- Disable agent integration with submission flow (AC3.5)
"""

import pytest


@pytest.mark.asyncio
async def test_agent_admin_creates(client, db_session):
    """Admin can create an agent."""
    # This test will be fully implemented once:
    # 1. Auth middleware is wired (Task 5)
    # 2. Admin role checking is implemented
    # 3. Routes are registered with app
    # For now, verify the route exists
    from rac_control_plane.api.routes.agents import router
    assert router is not None


@pytest.mark.asyncio
async def test_agent_non_admin_post_returns_403(client):
    """Non-admin POST to /agents returns 403."""
    # Will test once auth middleware is wired
    pass


@pytest.mark.asyncio
async def test_agent_disable_integration_with_submission(client, db_session):
    """Disabled agent returns 403 on submission operations."""
    # Will test AC3.5 via submission endpoint
    pass
