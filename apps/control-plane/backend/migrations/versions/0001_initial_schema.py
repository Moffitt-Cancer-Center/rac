"""Initial schema with all v1 tables and append-only grants.

Revision ID: 0001
Revises:
Create Date: 2026-04-23

This migration creates:
- pg_uuidv7 extension (IDEMPOTENT)
- submission_status ENUM
- 15 tables as per design
- Indexes on foreign keys and common filters
- APPEND-ONLY grants for audit tables (access_log, approval_event, revoked_token, detection_finding)

CRITICAL: rac_app role must exist before this migration runs.
If missing, the REVOKE statements will fail with a clear error message.
Created by: Tier 1 bootstrap (docs/runbooks/bootstrap.md)
"""
from alembic import op
import sqlalchemy as sa

# Alembic revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create extension if not exists
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_uuidv7;")

    # Create uuidv7() alias for uuid_generate_v7()
    # ghcr.io/fboulnois/pg_uuidv7 exposes uuid_generate_v7(), not uuidv7().
    # Add a uuidv7() wrapper so table DDL and application code can use the shorter name.
    op.execute("""
        CREATE OR REPLACE FUNCTION uuidv7()
        RETURNS uuid
        LANGUAGE sql
        VOLATILE STRICT PARALLEL SAFE
        AS $$ SELECT uuid_generate_v7() $$;
    """)

    # Create submission_status enum via raw DDL — avoids SQLAlchemy's double-create
    # bug where sa.Enum._on_table_create fires even with create_type=False
    # because the generic sa.Enum converts to a dialect-specific ENUM that resets create_type.
    op.execute(
        "CREATE TYPE submission_status AS ENUM ("
        "'awaiting_scan', 'pipeline_error', 'scan_rejected', 'needs_user_action', "
        "'needs_assistance', 'awaiting_research_review', 'research_rejected', "
        "'awaiting_it_review', 'it_rejected', 'approved', 'deployed'"
        ");"
    )

    # Use postgresql.ENUM (native, not generic sa.Enum) with create_type=False
    # so the column references the already-created type without re-creating it.
    from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
    submission_status_col_type = PG_ENUM(
        'awaiting_scan',
        'pipeline_error',
        'scan_rejected',
        'needs_user_action',
        'needs_assistance',
        'awaiting_research_review',
        'research_rejected',
        'awaiting_it_review',
        'it_rejected',
        'approved',
        'deployed',
        name='submission_status',
        create_type=False,
    )

    op.create_table(
        'submission',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('slug', sa.String(40), nullable=False),
        sa.Column('status', submission_status_col_type, nullable=False, server_default='awaiting_scan'),
        sa.Column('submitter_principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', sa.UUID(as_uuid=True), nullable=True),
        sa.Column('app_id', sa.UUID(as_uuid=True), nullable=True),
        sa.Column('github_repo_url', sa.String(255), nullable=False),
        sa.Column('git_ref', sa.String(255), server_default='main'),
        sa.Column('dockerfile_path', sa.String(255), server_default='Dockerfile'),
        sa.Column('pi_principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('dept_fallback', sa.String(255), nullable=False),
        sa.Column('manifest', sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_submission'),
    )
    op.create_index('idx_submission_slug', 'submission', ['slug'])
    op.create_index('idx_submission_status', 'submission', ['status'])
    op.create_index('idx_submission_submitter_principal_id', 'submission', ['submitter_principal_id'])
    op.create_index('idx_submission_app_id', 'submission', ['app_id'])
    op.create_index('idx_submission_created_at', 'submission', ['created_at'])

    # Create agent table (must exist before submission FK)
    op.create_table(
        'agent',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('entra_app_id', sa.String(36), nullable=False, unique=True),
        sa.Column('service_principal_id', sa.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('metadata', sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_agent'),
    )

    # Add FK from submission to agent
    op.create_foreign_key(
        'fk_submission_agent_id_agent',
        'submission', 'agent',
        ['agent_id'], ['id'],
        ondelete='RESTRICT'
    )

    # Create app table
    op.create_table(
        'app',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('submission_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('registry_url', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submission.id'], name='fk_app_submission_id_submission', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_app'),
    )
    op.create_index('idx_app_submission_id', 'app', ['submission_id'])

    # Add FK from submission to app
    op.create_foreign_key(
        'fk_submission_app_id_app',
        'submission', 'app',
        ['app_id'], ['id'],
        ondelete='RESTRICT'
    )

    # Create asset table
    op.create_table(
        'asset',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('app_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('blob_path', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['app.id'], name='fk_asset_app_id_app', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_asset'),
    )
    op.create_index('idx_asset_app_id', 'asset', ['app_id'])

    # Create scan_result table
    op.create_table(
        'scan_result',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('app_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('findings', sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['app.id'], name='fk_scan_result_app_id_app', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_scan_result'),
    )
    op.create_index('idx_scan_result_app_id', 'scan_result', ['app_id'])

    # Create detection_finding table (APPEND-ONLY)
    op.create_table(
        'detection_finding',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('submission_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(100), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submission.id'], name='fk_detection_finding_submission_id_submission', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_detection_finding'),
    )
    op.create_index('idx_detection_finding_submission_id', 'detection_finding', ['submission_id'])
    op.create_index('idx_detection_finding_created_at', 'detection_finding', ['created_at'])

    # Create approval_event table (APPEND-ONLY)
    op.create_table(
        'approval_event',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('submission_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('actor_principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('decision', sa.String(20), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submission.id'], name='fk_approval_event_submission_id_submission', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_approval_event'),
    )
    op.create_index('idx_approval_event_submission_id', 'approval_event', ['submission_id'])
    op.create_index('idx_approval_event_created_at', 'approval_event', ['created_at'])

    # Create reviewer_token table
    op.create_table(
        'reviewer_token',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('jti', sa.String(255), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_reviewer_token'),
    )

    # Create revoked_token table (APPEND-ONLY)
    op.create_table(
        'revoked_token',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('jti', sa.String(255), nullable=False, unique=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_revoked_token'),
    )

    # Create access_log table (APPEND-ONLY)
    op.create_table(
        'access_log',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('reviewer_token_jti', sa.String(255), nullable=True),
        sa.Column('submission_id', sa.UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['reviewer_token_jti'], ['reviewer_token.jti'], name='fk_access_log_reviewer_token_jti_reviewer_token', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['submission_id'], ['submission.id'], name='fk_access_log_submission_id_submission', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_access_log'),
    )
    op.create_index('idx_access_log_reviewer_token_jti', 'access_log', ['reviewer_token_jti'])
    op.create_index('idx_access_log_submission_id', 'access_log', ['submission_id'])
    op.create_index('idx_access_log_created_at', 'access_log', ['created_at'], postgresql_ops={'created_at': 'DESC'})

    # Create signing_key_version table
    op.create_table(
        'signing_key_version',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('kv_version_id', sa.String(255), nullable=False),
        sa.Column('algorithm', sa.String(20), server_default='RS256'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_signing_key_version'),
    )

    # Create webhook_subscription table
    op.create_table(
        'webhook_subscription',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.String(255), nullable=False),
        sa.Column('filter', sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column('secret', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_webhook_subscription'),
    )

    # Create cost_snapshot_monthly table
    op.create_table(
        'cost_snapshot_monthly',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('app_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['app.id'], name='fk_cost_snapshot_monthly_app_id_app', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='pk_cost_snapshot_monthly'),
        sa.UniqueConstraint('app_id', 'year', 'month', name='uq_cost_snapshot_app_ym'),
    )
    op.create_index('idx_cost_snapshot_monthly_app_id', 'cost_snapshot_monthly', ['app_id'])

    # Create shared_reference_catalog table
    op.create_table(
        'shared_reference_catalog',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('registry_url', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_shared_reference_catalog'),
    )

    # Create idempotency_key table
    op.create_table(
        'idempotency_key',
        sa.Column('id', sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column('key', sa.String(256), nullable=False),
        sa.Column('principal_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('response_status', sa.Integer(), nullable=False),
        sa.Column('response_body', sa.Text(), nullable=False),
        sa.Column('response_headers', sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_idempotency_key'),
        sa.UniqueConstraint('key', 'principal_id', name='uq_idempotency_key_principal'),
    )
    op.create_index('idx_idempotency_key_key', 'idempotency_key', ['key'])
    op.create_index('idx_idempotency_key_created_at', 'idempotency_key', ['created_at'])

    # Apply append-only grants (rac_app role cannot UPDATE/DELETE on these tables)
    # Wrap in DO block to check role exists and provide clear error if missing
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = 'rac_app') THEN
                RAISE EXCEPTION 'rac_app role must be created by Tier 1 bootstrap before running this migration. See docs/runbooks/bootstrap.md';
            END IF;
        END $$;
    """)

    # REVOKE UPDATE and DELETE on append-only tables
    op.execute("REVOKE UPDATE, DELETE ON access_log FROM rac_app;")
    op.execute("REVOKE UPDATE, DELETE ON approval_event FROM rac_app;")
    op.execute("REVOKE UPDATE, DELETE ON revoked_token FROM rac_app;")
    op.execute("REVOKE UPDATE, DELETE ON detection_finding FROM rac_app;")


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table('idempotency_key')
    op.drop_table('shared_reference_catalog')
    op.drop_table('cost_snapshot_monthly')
    op.drop_table('webhook_subscription')
    op.drop_table('signing_key_version')
    op.drop_table('access_log')
    op.drop_table('revoked_token')
    op.drop_table('reviewer_token')
    op.drop_table('approval_event')
    op.drop_table('detection_finding')
    op.drop_table('scan_result')
    op.drop_table('asset')
    op.drop_table('app')
    op.drop_table('submission')
    op.drop_table('agent')

    # Drop ENUM
    op.execute("DROP TYPE IF EXISTS submission_status;")

    # Drop extension (idempotent)
    op.execute("DROP EXTENSION IF EXISTS pg_uuidv7;")
