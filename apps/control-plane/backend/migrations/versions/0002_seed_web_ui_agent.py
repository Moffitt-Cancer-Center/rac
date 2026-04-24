"""Seed web-ui agent for Control Plane frontend authentication.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23

This migration inserts the special 'web-ui' agent that represents the Control Plane's
own frontend. This allows submissions via the web UI to be attributed to an agent,
enabling consistent audit trails even for interactive (non-API) users.

The web-ui agent:
- Kind: 'ui'
- Entra app ID: Uses a well-known placeholder UUID for this phase
- Service principal ID: Same as entra_app_id for simplicity
- Enabled: true
"""

from alembic import op
import sqlalchemy as sa
from uuid import UUID

# Alembic revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

# Well-known UUID for the web-ui agent (this phase)
WEB_UI_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")
WEB_UI_ENTRA_APP_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # Insert the web-ui agent
    op.execute(
        f"""
        INSERT INTO agent (id, name, kind, entra_app_id, service_principal_id, enabled, created_at, updated_at)
        VALUES (
            '{WEB_UI_AGENT_ID}'::uuid,
            'RAC Control Plane UI',
            'ui',
            '{WEB_UI_ENTRA_APP_ID}',
            '{WEB_UI_AGENT_ID}'::uuid,
            true,
            now(),
            now()
        )
        ON CONFLICT (entra_app_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    # Remove the web-ui agent
    op.execute(
        f"""
        DELETE FROM agent WHERE entra_app_id = '{WEB_UI_ENTRA_APP_ID}';
        """
    )
