# pattern: Functional Core
"""Tests for client-credentials authentication.

Verifies AC3.1 (agent auth works) and AC3.5 (disabled agents return 403).
"""

import pytest


@pytest.mark.asyncio
async def test_client_credentials_enabled_agent(client):
    """Enabled agent can authenticate and access protected route."""
    # This test will be implemented with Task 8 fixtures
    pass


@pytest.mark.asyncio
async def test_client_credentials_unknown_agent(client):
    """Unknown agent returns 403."""
    # This test will be implemented with Task 8 fixtures
    pass


@pytest.mark.asyncio
async def test_client_credentials_disabled_agent(client):
    """Disabled agent returns 403."""
    # This test will be implemented with Task 8 fixtures
    pass
