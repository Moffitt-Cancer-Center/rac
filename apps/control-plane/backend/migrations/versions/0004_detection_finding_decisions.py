"""Detection finding: replace placeholder columns with rule-engine schema + decisions table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-24

Changes:
  - Drop placeholder columns (kind, description) from detection_finding
  - Add rule-engine columns: rule_id, rule_version, title, detail, file_path,
    line_ranges (JSONB), auto_fix (JSONB)
  - Create detection_finding_decision_decision ENUM type
  - Create detection_finding_decision table (append-only)
  - Index on detection_finding_decision.detection_finding_id

AC12.1: detection_finding_decision is append-only;
REVOKE UPDATE, DELETE for rac_app role applied at end of migration.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- Modify detection_finding table ---
    # Drop placeholder columns from Phase 2
    op.drop_column("detection_finding", "kind")
    op.drop_column("detection_finding", "description")

    # Drop the old submission_id index (we'll recreate it)
    op.drop_index("idx_detection_finding_submission_id", table_name="detection_finding")

    # Add rule-engine columns
    op.add_column("detection_finding", sa.Column("rule_id", sa.String(200), nullable=False, server_default="unknown"))
    op.add_column("detection_finding", sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("detection_finding", sa.Column("title", sa.String(500), nullable=False, server_default=""))
    op.add_column("detection_finding", sa.Column("detail", sa.Text(), nullable=False, server_default=""))
    op.add_column("detection_finding", sa.Column("file_path", sa.String(512), nullable=True))
    op.add_column("detection_finding", sa.Column("line_ranges", JSONB, nullable=True))
    op.add_column("detection_finding", sa.Column("auto_fix", JSONB, nullable=True))

    # Remove server defaults now that existing rows are filled
    op.alter_column("detection_finding", "rule_id", server_default=None)
    op.alter_column("detection_finding", "rule_version", server_default=None)
    op.alter_column("detection_finding", "title", server_default=None)
    op.alter_column("detection_finding", "detail", server_default=None)

    # Recreate submission_id index
    op.create_index("idx_detection_finding_submission_id", "detection_finding", ["submission_id"])

    # --- Create detection_finding_decision_decision ENUM ---
    op.execute(
        "CREATE TYPE detection_finding_decision_decision AS ENUM "
        "('accept', 'override', 'auto_fix', 'dismiss');"
    )

    # --- Create detection_finding_decision table (append-only) ---
    from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
    decision_col_type = PG_ENUM(
        "accept", "override", "auto_fix", "dismiss",
        name="detection_finding_decision_decision",
        create_type=False,
    )

    op.create_table(
        "detection_finding_decision",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("detection_finding_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", decision_col_type, nullable=False),
        sa.Column("decision_actor_principal_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["detection_finding_id"],
            ["detection_finding.id"],
            name="fk_dfd_finding_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_detection_finding_decision"),
    )

    op.create_index(
        "idx_detection_finding_decision_detection_finding_id",
        "detection_finding_decision",
        ["detection_finding_id"],
    )
    op.create_index(
        "idx_detection_finding_decision_created_at",
        "detection_finding_decision",
        ["created_at"],
    )

    # Append-only: REVOKE UPDATE, DELETE for rac_app role
    op.execute("REVOKE UPDATE, DELETE ON detection_finding_decision FROM rac_app;")


def downgrade() -> None:
    op.drop_table("detection_finding_decision")
    op.execute("DROP TYPE IF EXISTS detection_finding_decision_decision;")

    # Restore detection_finding to Phase 2 schema
    op.drop_column("detection_finding", "auto_fix")
    op.drop_column("detection_finding", "line_ranges")
    op.drop_column("detection_finding", "file_path")
    op.drop_column("detection_finding", "detail")
    op.drop_column("detection_finding", "title")
    op.drop_column("detection_finding", "rule_version")
    op.drop_column("detection_finding", "rule_id")

    op.add_column("detection_finding", sa.Column("kind", sa.String(100), nullable=False, server_default="unknown"))
    op.add_column("detection_finding", sa.Column("description", sa.Text(), nullable=False, server_default=""))
