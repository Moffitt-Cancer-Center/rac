"""Rebuild webhook_subscription + scan_result + approval_event for Phase 3.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-23

Changes:
- Drops old webhook_subscription (principal_id/url/filter/secret columns) and
  recreates with name, callback_url, event_types JSONB, secret_name, enabled,
  consecutive_failures, last_delivery_at, secret_rotated_at.
- Drops old scan_result (app_id/kind columns) and recreates with submission_id,
  verdict, effective_severity, findings JSONB, artifact URIs, image_digest,
  image_ref, defender_timed_out.
- Adds payload JSONB column to approval_event.
- Makes approval_event.actor_principal_id nullable (system events have no actor).
"""
from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── webhook_subscription: drop old, recreate new ─────────────────────────
    op.drop_table("webhook_subscription")
    op.create_table(
        "webhook_subscription",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("callback_url", sa.String(255), nullable=False),
        sa.Column("event_types", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("secret_name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("secret_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_subscription"),
    )

    # ── scan_result: drop old, recreate new ──────────────────────────────────
    op.drop_table("scan_result")
    op.create_table(
        "scan_result",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("submission_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("verdict", sa.String(50), nullable=False),
        sa.Column("effective_severity", sa.String(20), nullable=False),
        sa.Column("findings", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("build_log_uri", sa.String(512), nullable=True),
        sa.Column("sbom_uri", sa.String(512), nullable=True),
        sa.Column("grype_report_uri", sa.String(512), nullable=True),
        sa.Column("defender_report_uri", sa.String(512), nullable=True),
        sa.Column("image_digest", sa.String(255), nullable=True),
        sa.Column("image_ref", sa.String(255), nullable=True),
        sa.Column("defender_timed_out", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submission.id"],
            name="fk_scan_result_submission_id_submission",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_result"),
    )
    op.create_index("idx_scan_result_submission_id", "scan_result", ["submission_id"])

    # ── approval_event: make actor_principal_id nullable + add payload ────────
    op.alter_column("approval_event", "actor_principal_id", nullable=True)
    op.add_column(
        "approval_event",
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_event", "payload")
    op.alter_column("approval_event", "actor_principal_id", nullable=False)

    op.drop_table("scan_result")
    op.create_table(
        "scan_result",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("app_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("findings", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["app_id"], ["app.id"],
            name="fk_scan_result_app_id_app",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_result"),
    )
    op.create_index("idx_scan_result_app_id", "scan_result", ["app_id"])

    op.drop_table("webhook_subscription")
    op.create_table(
        "webhook_subscription",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("principal_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(255), nullable=False),
        sa.Column("filter", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_subscription"),
    )
