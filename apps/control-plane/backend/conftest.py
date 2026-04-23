"""Root conftest for RAC Control Plane tests.

Imports fixture modules to make fixtures available to all tests.
"""

# Import fixtures to make them available
from tests.fixtures.db import db_session, migrated_db, pg_dsn, postgres_container  # noqa: F401
from tests.fixtures.oidc import mock_oidc  # noqa: F401
from tests.fixtures.client import app, client  # noqa: F401

__all__ = [
    "postgres_container",
    "pg_dsn",
    "migrated_db",
    "db_session",
    "mock_oidc",
    "app",
    "client",
]
