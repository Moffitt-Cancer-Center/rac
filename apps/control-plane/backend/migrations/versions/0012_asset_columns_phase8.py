"""Extend asset table with Phase 8 columns for full asset lifecycle support.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-23

The Phase 2 migration (0001) created asset with a minimal schema:
  id, app_id, kind, blob_path, created_at

Phase 8 requires:
  - submission_id  UUID  FK → submission (the submission this asset belongs to)
  - name           TEXT  logical asset name
  - mount_path     TEXT  absolute path inside container
  - status         TEXT  pending|ready|hash_mismatch|unreachable
  - sha256         TEXT  verified sha256 hex digest (nullable until finalized)
  - size_bytes     BIGINT file size in bytes (nullable)
  - blob_uri       TEXT  full blob URI (nullable until upload finalized)
  - expected_sha256 TEXT declared sha256 (for mismatch display)
  - actual_sha256   TEXT computed sha256 (for mismatch display)

  app_id is made nullable (assets belong to submissions, not yet to deployed apps).
  blob_path is made nullable (external_url assets may not be cached to blob).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Add submission_id column (nullable first, we backfill nothing since table is empty in prod)
    op.add_column("asset", sa.Column("submission_id", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_asset_submission_id_submission",
        "asset",
        "submission",
        ["submission_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("idx_asset_submission_id", "asset", ["submission_id"])

    # Add name column
    op.add_column("asset", sa.Column("name", sa.String(255), nullable=True))

    # Add mount_path column
    op.add_column("asset", sa.Column("mount_path", sa.String(512), nullable=True))

    # Add status column (default 'pending')
    op.add_column(
        "asset",
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
    )

    # Add sha256 column (nullable — computed after upload)
    op.add_column("asset", sa.Column("sha256", sa.String(64), nullable=True))

    # Add size_bytes column (nullable)
    op.add_column("asset", sa.Column("size_bytes", sa.BigInteger(), nullable=True))

    # Add blob_uri column (nullable — not all assets go through blob staging)
    op.add_column("asset", sa.Column("blob_uri", sa.String(1024), nullable=True))

    # Add expected_sha256 column (the declared/expected sha256, for mismatch display)
    op.add_column("asset", sa.Column("expected_sha256", sa.String(64), nullable=True))

    # Add actual_sha256 column (the computed sha256, for mismatch display)
    op.add_column("asset", sa.Column("actual_sha256", sa.String(64), nullable=True))

    # Make blob_path nullable (external_url assets may not use it)
    op.alter_column("asset", "blob_path", existing_type=sa.String(255), nullable=True)

    # Make app_id nullable (assets are tied to submissions, not apps directly)
    op.alter_column("asset", "app_id", existing_type=sa.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("asset", "app_id", existing_type=sa.UUID(as_uuid=True), nullable=False)
    op.alter_column("asset", "blob_path", existing_type=sa.String(255), nullable=False)
    op.drop_column("asset", "actual_sha256")
    op.drop_column("asset", "expected_sha256")
    op.drop_column("asset", "blob_uri")
    op.drop_column("asset", "size_bytes")
    op.drop_column("asset", "sha256")
    op.drop_column("asset", "status")
    op.drop_column("asset", "mount_path")
    op.drop_column("asset", "name")
    op.drop_index("idx_asset_submission_id", "asset")
    op.drop_constraint("fk_asset_submission_id_submission", "asset", type_="foreignkey")
    op.drop_column("asset", "submission_id")
