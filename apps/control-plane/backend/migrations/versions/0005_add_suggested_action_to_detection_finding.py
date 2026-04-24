"""Add suggested_action column to detection_finding.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-23

Changes:
  - ADD COLUMN IF NOT EXISTS suggested_action text NULL to detection_finding
    Stores the rule's suggested resolution action ('accept', 'override',
    'auto_fix', 'dismiss').  NULL means the rule did not specify one.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ADD COLUMN IF NOT EXISTS so repeated runs are safe
    op.execute(
        "ALTER TABLE detection_finding "
        "ADD COLUMN IF NOT EXISTS suggested_action VARCHAR(50) NULL;"
    )


def downgrade() -> None:
    op.drop_column("detection_finding", "suggested_action")
