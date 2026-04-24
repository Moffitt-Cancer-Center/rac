"""Align cost_snapshot_monthly to app_slug + year_month (YYYY-MM) schema.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-23

Changes:
  - Drops the Phase-2 placeholder cost_snapshot_monthly table (used app_id FK,
    year int, month int — inconsistent with AC11.2 spec which requires app_slug
    text and year_month text YYYY-MM for cost-export CSV ingestion).
  - Recreates cost_snapshot_monthly with the correct columns:
      id, app_slug, year_month, cost_usd, untagged_usd, created_at, updated_at.
  - Adds a UNIQUE (app_slug, year_month) constraint so the ingest upsert works.
  - Adds last_request_at column to app table (optional; populated by Shim in
    Phase 6; NULL means all deployed apps qualify as idle for Phase 5 testing).

Design deviation: the Phase-2 model used (app_id FK, year, month); the
cost-export ingest needs app_slug to correlate with Azure Tags (rac_app_slug)
without requiring a DB join at ingest time.  Approved deviation.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Drop the old table (no FK dependencies from other tables in Phase 2 schema).
    op.execute("DROP TABLE IF EXISTS cost_snapshot_monthly CASCADE;")

    # Recreate with the correct schema.
    op.execute("""
        CREATE TABLE cost_snapshot_monthly (
            id           UUID         NOT NULL DEFAULT uuidv7(),
            app_slug     VARCHAR(40)  NOT NULL,
            year_month   VARCHAR(7)   NOT NULL,  -- YYYY-MM
            cost_usd     NUMERIC(12,4) NOT NULL DEFAULT 0,
            untagged_usd NUMERIC(12,4) NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT pk_cost_snapshot_monthly PRIMARY KEY (id),
            CONSTRAINT uq_cost_snapshot_app_ym UNIQUE (app_slug, year_month)
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cost_snapshot_monthly_app_slug "
        "ON cost_snapshot_monthly (app_slug);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cost_snapshot_monthly_year_month "
        "ON cost_snapshot_monthly (year_month);"
    )

    # Add last_request_at to app table.
    # Populated by the Shim (Phase 6) when requests hit the proxy.
    # NULL for Phase 5 — all deployed apps qualify as idle.
    op.execute("""
        ALTER TABLE app
        ADD COLUMN IF NOT EXISTS last_request_at TIMESTAMPTZ DEFAULT NULL;
    """)

    # Grants for rac_app role (if it exists)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_app') THEN
                GRANT SELECT, INSERT, UPDATE ON cost_snapshot_monthly TO rac_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS last_request_at;")
    op.execute("DROP TABLE IF EXISTS cost_snapshot_monthly;")

    # Restore simplified Phase-2 table
    op.execute("""
        CREATE TABLE cost_snapshot_monthly (
            id         UUID        NOT NULL DEFAULT uuidv7(),
            app_id     UUID        NOT NULL REFERENCES app(id) ON DELETE RESTRICT,
            year       INTEGER     NOT NULL,
            month      INTEGER     NOT NULL,
            cost_usd   FLOAT       NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT pk_cost_snapshot_monthly PRIMARY KEY (id),
            CONSTRAINT uq_cost_snapshot_app_ym UNIQUE (app_id, year, month)
        )
    """)
