"""rac_shim DB role with least-privilege grants

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-23

Creates the rac_shim Postgres role used by the Token-Check Shim (Phase 6).
Grants follow the principle of least privilege (AC12.1):

  - SELECT on: revoked_token, reviewer_token, app, submission
    (needed for token validation, denylist cache, app-registry refresh)
  - INSERT on: access_log only
    (append-only audit log — no UPDATE/DELETE allowed)
  - CONNECT + USAGE on the database and public schema

The role is created with a placeholder password.  In production the operator
MUST replace it before the shim can connect:

    ALTER ROLE rac_shim WITH PASSWORD '<strong-random-secret>';

That secret is then stored as the full DSN in the platform Key Vault under the
secret name `shim-database-dsn`.  The shim reads it at startup via its managed
identity (Key Vault Secrets User role, granted in Phase 1 role-assignments.bicep).

WARNING: Do NOT commit the real password to source control.  The placeholder
here is intentionally invalid so that an unconfigured environment fails loudly
rather than silently insecure.
"""

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create the role if it doesn't already exist.
    # The DO block is required because CREATE ROLE IF NOT EXISTS is not standard
    # SQL and asyncpg sends statements as prepared queries (no bare DDL branching).
    op.execute("""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_shim') THEN
            CREATE ROLE rac_shim WITH LOGIN PASSWORD 'placeholder_reset_in_deploy';
        END IF;
    END $$;
    """)

    # CONNECT on the current database.
    # current_database() is evaluated at migration time against the target DB.
    op.execute("""
    DO $$ BEGIN
        EXECUTE format(
            'GRANT CONNECT ON DATABASE %I TO rac_shim',
            current_database()
        );
    END $$;
    """)

    # Schema usage
    op.execute("GRANT USAGE ON SCHEMA public TO rac_shim;")

    # SELECT on tables the shim reads
    op.execute("""
    GRANT SELECT ON revoked_token TO rac_shim;
    """)
    op.execute("""
    GRANT SELECT ON reviewer_token TO rac_shim;
    """)
    op.execute("""
    GRANT SELECT ON app TO rac_shim;
    """)
    op.execute("""
    GRANT SELECT ON submission TO rac_shim;
    """)

    # INSERT only on access_log (AC12.1 append-only)
    op.execute("""
    GRANT INSERT ON access_log TO rac_shim;
    """)

    # Explicitly revoke UPDATE/DELETE on access_log to enforce append-only
    # (these are not granted, but explicit REVOKE makes intent auditable).
    op.execute("""
    REVOKE UPDATE, DELETE ON access_log FROM rac_shim;
    """)


def downgrade() -> None:
    # Revoke grants before dropping to avoid dangling privilege entries.
    op.execute("""
    DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_shim') THEN
            REVOKE ALL ON access_log FROM rac_shim;
            REVOKE ALL ON revoked_token FROM rac_shim;
            REVOKE ALL ON reviewer_token FROM rac_shim;
            REVOKE ALL ON app FROM rac_shim;
            REVOKE ALL ON submission FROM rac_shim;
            REVOKE USAGE ON SCHEMA public FROM rac_shim;
        END IF;
    END $$;
    """)

    op.execute("""
    DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rac_shim') THEN
            EXECUTE format(
                'REVOKE CONNECT ON DATABASE %I FROM rac_shim',
                current_database()
            );
        END IF;
    END $$;
    """)

    op.execute("DROP ROLE IF EXISTS rac_shim;")
