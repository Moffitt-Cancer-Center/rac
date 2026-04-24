"""Extend access_log table with Phase 6/7 shim-written columns.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-23

The Phase 2 migration created access_log with a minimal schema.
Phase 6 (Shim) writes richer columns via asyncpg COPY.  This migration
aligns the Postgres schema with what the Shim's batch_writer expects so
that the Control Plane can also query and display the full access log.

New columns (all nullable for backward compat with legacy rows):
  - app_id        UUID  (FK → app.id)
  - access_mode   TEXT
  - host          TEXT
  - method        TEXT
  - upstream_status INT
  - latency_ms    INT
  - user_agent    TEXT
  - request_id    UUID

The existing columns (principal_id, reviewer_token_jti, submission_id,
action) are kept unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE access_log
            ADD COLUMN IF NOT EXISTS app_id UUID REFERENCES app(id) ON DELETE RESTRICT,
            ADD COLUMN IF NOT EXISTS access_mode VARCHAR(20),
            ADD COLUMN IF NOT EXISTS host TEXT,
            ADD COLUMN IF NOT EXISTS method VARCHAR(10),
            ADD COLUMN IF NOT EXISTS upstream_status INTEGER,
            ADD COLUMN IF NOT EXISTS latency_ms INTEGER,
            ADD COLUMN IF NOT EXISTS user_agent TEXT,
            ADD COLUMN IF NOT EXISTS request_id UUID,
            ADD COLUMN IF NOT EXISTS source_ip TEXT;
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_access_log_app_id "
        "ON access_log (app_id);"
    )

    # Grant SELECT on access_log to rac_app so the Control Plane can query it.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_app') THEN
                GRANT SELECT ON access_log TO rac_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_access_log_app_id;")
    op.execute("""
        ALTER TABLE access_log
            DROP COLUMN IF EXISTS source_ip,
            DROP COLUMN IF EXISTS request_id,
            DROP COLUMN IF EXISTS user_agent,
            DROP COLUMN IF EXISTS latency_ms,
            DROP COLUMN IF EXISTS upstream_status,
            DROP COLUMN IF EXISTS method,
            DROP COLUMN IF EXISTS host,
            DROP COLUMN IF EXISTS access_mode,
            DROP COLUMN IF EXISTS app_id;
    """)
