"""Seed web-ui agent for Control Plane frontend authentication.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23

Inserts the special 'web-ui' agent that represents the Control Plane's own
frontend so UI-originated submissions are attributed to an agent identity
consistently with API callers.

Values are sourced from environment variables populated at deploy time:
- RAC_WEB_UI_AGENT_ID: UUID of the agent row (must be a valid UUIDv7 or v4)
- RAC_WEB_UI_ENTRA_APP_ID: Entra Application (client) ID of the frontend SPA

If either is unset the migration is a no-op; operators can insert the agent
row manually later or re-run the migration once the env vars are set.
Document this in docs/runbooks/bootstrap.md under the Entra app registration
section.
"""

import os
from uuid import UUID

from alembic import op

# Alembic revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def _env_uuid(name: str) -> UUID | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return UUID(raw)
    except (ValueError, AttributeError):
        raise RuntimeError(
            f"{name} is set but is not a valid UUID: {raw!r}"
        ) from None


def upgrade() -> None:
    agent_id = _env_uuid("RAC_WEB_UI_AGENT_ID")
    entra_app_id = os.environ.get("RAC_WEB_UI_ENTRA_APP_ID")

    if agent_id is None or not entra_app_id:
        # Intentionally a no-op when env is not configured at migrate time.
        # Operator applies this row post-bootstrap once Entra values are known.
        return

    op.execute(
        f"""
        INSERT INTO agent
          (id, name, kind, entra_app_id, service_principal_id, enabled, created_at, updated_at)
        VALUES (
            '{agent_id}'::uuid,
            'RAC Control Plane UI',
            'ui',
            '{entra_app_id}',
            '{agent_id}'::uuid,
            true,
            now(),
            now()
        )
        ON CONFLICT (entra_app_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    entra_app_id = os.environ.get("RAC_WEB_UI_ENTRA_APP_ID")
    if not entra_app_id:
        return
    op.execute(
        f"DELETE FROM agent WHERE entra_app_id = '{entra_app_id}';"
    )
