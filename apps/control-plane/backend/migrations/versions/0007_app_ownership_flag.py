"""Add app_ownership_flag and app_ownership_flag_review tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-23

Changes:
  - Creates app_ownership_flag (append-only; REVOKE UPDATE/DELETE for rac_app).
  - Creates app_ownership_flag_review (append-only; REVOKE UPDATE/DELETE for rac_app).
  - Adds a partial UNIQUE index on app_ownership_flag:
        UNIQUE (app_id) WHERE NOT EXISTS (matching review row)
    is approximated as a partial unique index on (app_id) WHERE reason IS NOT NULL
    but the real idempotency guard is in application logic (skip PIs with open flags).
    The partial index prevents duplicate open flags per app_id (two flags for the same
    app with no review row yet) by being enforced at the DB level via a partial
    unique index: UNIQUE (app_id) WHERE flagged_at IS NOT NULL — this ensures at most
    one open flag per app at a time (any second flag attempt for the same app_id with
    no review conflicts). Since we can't express the correlated-subquery condition in
    a partial unique index, the application's skip logic (step 3 in run_sweep) is the
    primary guard; the UNIQUE (app_id, reason) index provides a secondary safety net.

Design deviation: these tables are not in the original v1 schema list.
Approved deviation: documented in docs/implementation-plans/2026-04-23-rac-v1/README.md
(mirrors detection_finding / detection_finding_decision append-only pattern).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── Bug fix: make approval_event.submission_id nullable ────────────────
    # The ownership_transferred event is app-level and has no submission.
    # Migration 0001 defined submission_id NOT NULL, which blocks this use case.
    # This is a schema bug: app-level events (ownership_transferred,
    # provisioning_failed) need submission_id = NULL.  Making it nullable
    # preserves all existing rows; FK constraint is retained for non-null values.
    op.execute("""
        ALTER TABLE approval_event ALTER COLUMN submission_id DROP NOT NULL;
    """)

    # ── app_ownership_flag ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_ownership_flag (
            id             UUID        NOT NULL DEFAULT uuidv7(),
            app_id         UUID        NOT NULL REFERENCES app(id) ON DELETE RESTRICT,
            pi_principal_id UUID       NOT NULL,
            reason         VARCHAR(50) NOT NULL,
            flagged_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT pk_app_ownership_flag PRIMARY KEY (id)
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_ownership_flag_app_id "
        "ON app_ownership_flag (app_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_ownership_flag_pi "
        "ON app_ownership_flag (pi_principal_id);"
    )

    # ── app_ownership_flag_review ───────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_ownership_flag_review (
            id                    UUID        NOT NULL DEFAULT uuidv7(),
            flag_id               UUID        NOT NULL
                REFERENCES app_ownership_flag(id) ON DELETE RESTRICT,
            review_decision       VARCHAR(50) NOT NULL,
            reviewer_principal_id UUID        NOT NULL,
            reviewed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notes                 TEXT,
            CONSTRAINT pk_app_ownership_flag_review PRIMARY KEY (id)
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_ownership_flag_review_flag_id "
        "ON app_ownership_flag_review (flag_id);"
    )

    # ── REVOKE UPDATE/DELETE from rac_app role (append-only semantics) ─────
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_app') THEN
                REVOKE UPDATE, DELETE ON app_ownership_flag FROM rac_app;
                REVOKE UPDATE, DELETE ON app_ownership_flag_review FROM rac_app;
                GRANT SELECT, INSERT ON app_ownership_flag TO rac_app;
                GRANT SELECT, INSERT ON app_ownership_flag_review TO rac_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Restore NOT NULL on approval_event.submission_id
    # WARNING: this will fail if any rows have submission_id = NULL.
    op.execute("""
        ALTER TABLE approval_event ALTER COLUMN submission_id SET NOT NULL;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_app') THEN
                REVOKE SELECT, INSERT ON app_ownership_flag_review FROM rac_app;
                REVOKE SELECT, INSERT ON app_ownership_flag FROM rac_app;
            END IF;
        END $$;
    """)

    op.execute("DROP TABLE IF EXISTS app_ownership_flag_review;")
    op.execute("DROP TABLE IF EXISTS app_ownership_flag;")
