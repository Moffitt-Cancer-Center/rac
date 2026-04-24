"""Shared pytest fixtures for RAC Control Plane tests.

Imports all fixtures from the fixtures/ sub-package so they are available
to all test modules without explicit imports.

Also provides session-wide autouse patches so that tests do not need real
Azure Graph credentials.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from tests.fixtures.client import app, client  # noqa: F401
from tests.fixtures.db import db_session, db_setup, migrated_db, pg_dsn, postgres_container  # noqa: F401
from tests.fixtures.oidc import mock_oidc  # noqa: F401


@pytest.fixture(autouse=True)
def auto_mock_graph_get_user(request: pytest.FixtureRequest):
    """Auto-patch graph_gateway.get_user to avoid real Graph calls.

    Returns a valid, active GraphUser by default.  Individual tests that need
    specific Graph behaviour (e.g. PI not found) can override by patching
    ``rac_control_plane.services.ownership.graph_gateway.get_user`` directly
    within the test body, or by using the ``mock_graph_user`` fixture.

    Skipped for tests that set the marker ``no_auto_graph_mock``.
    """
    if request.node.get_closest_marker("no_auto_graph_mock"):
        yield
        return

    from rac_control_plane.services.ownership.graph_gateway import GraphUser

    async def _always_ok(oid: UUID, *, client: object = None) -> GraphUser:  # type: ignore[misc]
        return GraphUser(
            oid=oid,
            account_enabled=True,
            display_name="Auto-mocked PI",
            user_principal_name=f"{oid}@example.com",
            department="Testing",
        )

    with patch(
        "rac_control_plane.services.ownership.graph_gateway.get_user",
        side_effect=_always_ok,
    ):
        yield
