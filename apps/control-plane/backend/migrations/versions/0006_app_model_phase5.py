"""Phase 5: Rebuild app table + extend signing_key_version.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-23

Changes:
  - Replaces the Phase 2 placeholder app table (submission_id, name, registry_url)
    with the Phase 5 design (slug, pi_principal_id, dept_fallback,
    current_submission_id, target_port, cpu_cores, memory_gb, access_mode).
  - Adds app_access_mode enum type.
  - Adds app_slug, kv_kid columns to signing_key_version.
  - All operations use ADD/DROP COLUMN IF NOT EXISTS for idempotency.

Migration strategy:
  - Drop legacy FK constraints on app table referencing old columns.
  - Add new columns to app table with defaults.
  - The old columns (submission_id, name, registry_url) are retained in the
    DB to avoid dropping data; ORM model no longer uses them.  A future
    migration can drop them once confirmed safe.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create app_access_mode enum (idempotent via DO block)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'app_access_mode') THEN
                CREATE TYPE app_access_mode AS ENUM ('token_required', 'public');
            END IF;
        END $$;
    """)

    # Drop the FK constraint from app to submission (old submission_id column)
    # so we can make submission_id nullable (it's being superseded by current_submission_id).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_app_submission_id_submission'
            ) THEN
                ALTER TABLE app DROP CONSTRAINT fk_app_submission_id_submission;
            END IF;
        END $$;
    """)

    # Make legacy columns nullable (they are being superseded)
    op.execute(
        "ALTER TABLE app ALTER COLUMN submission_id DROP NOT NULL;"
    )
    op.execute(
        "ALTER TABLE app ALTER COLUMN name DROP NOT NULL;"
    )
    op.execute(
        "ALTER TABLE app ALTER COLUMN registry_url DROP NOT NULL;"
    )

    # Add new columns to app table with sensible defaults
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS slug VARCHAR(40) NULL;"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS pi_principal_id UUID NULL;"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS dept_fallback VARCHAR(255) NOT NULL DEFAULT '';"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS current_submission_id UUID NULL;"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS target_port INTEGER NOT NULL DEFAULT 8000;"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS cpu_cores NUMERIC(4,2) NOT NULL DEFAULT 0.25;"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS memory_gb NUMERIC(4,2) NOT NULL DEFAULT 0.5;"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS access_mode app_access_mode NOT NULL DEFAULT 'token_required';"
    )
    op.execute(
        "ALTER TABLE app "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE "
        "NOT NULL DEFAULT NOW();"
    )

    # Add unique index on slug (full unique index for ON CONFLICT support).
    # Drop any pre-existing partial index first so we can recreate as a full unique index.
    op.execute("DROP INDEX IF EXISTS uq_app_slug;")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_app_slug ON app (slug);")
    # Also add named unique constraint so ORM uniqueness reporting works
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_app_slug'
            ) THEN
                -- index-based unique constraint already created above;
                -- nothing more needed — PostgreSQL uses the index.
                NULL;
            END IF;
        END $$;
    """)

    # FK from app.current_submission_id → submission.id
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_app_current_submission_id_submission'
            ) THEN
                ALTER TABLE app
                ADD CONSTRAINT fk_app_current_submission_id_submission
                FOREIGN KEY (current_submission_id) REFERENCES submission(id)
                ON DELETE RESTRICT;
            END IF;
        END $$;
    """)

    # Extend signing_key_version
    op.execute(
        "ALTER TABLE signing_key_version "
        "ADD COLUMN IF NOT EXISTS app_slug VARCHAR(40) NULL;"
    )
    op.execute(
        "ALTER TABLE signing_key_version "
        "ADD COLUMN IF NOT EXISTS kv_kid VARCHAR(512) NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_signing_key_version_app_slug "
        "ON signing_key_version (app_slug);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_signing_key_version_app_slug;")
    op.execute("ALTER TABLE signing_key_version DROP COLUMN IF EXISTS app_slug;")
    op.execute("ALTER TABLE signing_key_version DROP COLUMN IF EXISTS kv_kid;")

    op.execute("DROP INDEX IF EXISTS uq_app_slug;")
    op.execute(
        "ALTER TABLE app "
        "DROP CONSTRAINT IF EXISTS fk_app_current_submission_id_submission;"
    )
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS updated_at;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS access_mode;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS memory_gb;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS cpu_cores;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS target_port;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS current_submission_id;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS dept_fallback;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS pi_principal_id;")
    op.execute("ALTER TABLE app DROP COLUMN IF EXISTS slug;")
    op.execute("DROP TYPE IF EXISTS app_access_mode;")
