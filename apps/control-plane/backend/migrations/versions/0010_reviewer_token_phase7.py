"""Extend reviewer_token and revoked_token for Phase 7 token management.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-23

Changes:
  - reviewer_token: adds app_id (FK to app), reviewer_label, kid,
      issued_by_principal_id, scope (default 'read').
  - revoked_token: adds revoked_by_principal_id, reason, expires_at.
  - Grants INSERT on reviewer_token and revoked_token to rac_app role
    (append-only; UPDATE/DELETE revoked).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── reviewer_token: add Phase 7 columns ────────────────────────────────
    op.execute("""
        ALTER TABLE reviewer_token
            ADD COLUMN IF NOT EXISTS app_id UUID REFERENCES app(id) ON DELETE RESTRICT,
            ADD COLUMN IF NOT EXISTS reviewer_label VARCHAR(100),
            ADD COLUMN IF NOT EXISTS kid VARCHAR(255),
            ADD COLUMN IF NOT EXISTS issued_by_principal_id UUID,
            ADD COLUMN IF NOT EXISTS scope VARCHAR(50) NOT NULL DEFAULT 'read';
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviewer_token_app_id "
        "ON reviewer_token (app_id);"
    )

    # ── revoked_token: add Phase 7 columns ─────────────────────────────────
    op.execute("""
        ALTER TABLE revoked_token
            ADD COLUMN IF NOT EXISTS revoked_by_principal_id UUID,
            ADD COLUMN IF NOT EXISTS reason TEXT,
            ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
    """)

    # ── Grants for rac_app role ─────────────────────────────────────────────
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_app') THEN
                GRANT SELECT, INSERT ON reviewer_token TO rac_app;
                REVOKE UPDATE, DELETE ON reviewer_token FROM rac_app;
                GRANT SELECT, INSERT ON revoked_token TO rac_app;
                REVOKE UPDATE, DELETE ON revoked_token FROM rac_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE revoked_token
            DROP COLUMN IF EXISTS expires_at,
            DROP COLUMN IF EXISTS reason,
            DROP COLUMN IF EXISTS revoked_by_principal_id;
    """)

    op.execute("DROP INDEX IF EXISTS idx_reviewer_token_app_id;")

    op.execute("""
        ALTER TABLE reviewer_token
            DROP COLUMN IF EXISTS scope,
            DROP COLUMN IF EXISTS issued_by_principal_id,
            DROP COLUMN IF EXISTS kid,
            DROP COLUMN IF EXISTS reviewer_label,
            DROP COLUMN IF EXISTS app_id;
    """)
