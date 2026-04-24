# pattern: Imperative Shell
"""SQLAlchemy ORM models for all v1 tables.

Note: ORM models are classified as Imperative Shell because they carry
session-coupled behavior (lazy loads, identity map, expire_on_commit).
Tests must use async_sessionmaker(expire_on_commit=False) and avoid
triggering lazy loads.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SubmissionStatus(StrEnum):
    """FSM states for submission lifecycle."""
    awaiting_scan = "awaiting_scan"
    pipeline_error = "pipeline_error"
    scan_rejected = "scan_rejected"
    needs_user_action = "needs_user_action"
    needs_assistance = "needs_assistance"
    awaiting_research_review = "awaiting_research_review"
    research_rejected = "research_rejected"
    awaiting_it_review = "awaiting_it_review"
    it_rejected = "it_rejected"
    approved = "approved"
    deployed = "deployed"


naming_convention = {
    "ix": "idx_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    metadata = MetaData(naming_convention=naming_convention)


class Submission(Base):
    """Application submission record."""
    __tablename__ = "submission"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    slug: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status", native_enum=True, create_type=False),
        nullable=False,
        index=True,
        default=SubmissionStatus.awaiting_scan,
    )
    submitter_principal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent.id", ondelete="RESTRICT"),
        nullable=True,
    )
    app_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    github_repo_url: Mapped[str] = mapped_column(String(255), nullable=False)
    git_ref: Mapped[str] = mapped_column(String(255), default="main")
    dockerfile_path: Mapped[str] = mapped_column(String(255), default="Dockerfile")
    pi_principal_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    dept_fallback: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AccessMode(StrEnum):
    """App access mode."""
    token_required = "token_required"  # noqa: S105
    public = "public"


class App(Base):
    """Application record: keyed on slug, tracks current approved submission.

    Phase 5 extends the original minimal App model with full provisioning fields.
    Migration 0006 adds the new columns.
    """
    __tablename__ = "app"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    pi_principal_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    dept_fallback: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_submission_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("submission.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_port: Mapped[int] = mapped_column(nullable=False, default=8000)
    cpu_cores: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=0.25)
    memory_gb: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=0.5)
    access_mode: Mapped[AccessMode] = mapped_column(
        Enum(AccessMode, name="app_access_mode", native_enum=True, create_type=False),
        nullable=False,
        default=AccessMode.token_required,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Asset(Base):
    """File/artifact attached to app."""
    __tablename__ = "asset"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    app_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    blob_path: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ScanResult(Base):
    """Scan result from the build/scan pipeline."""
    __tablename__ = "scan_result"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    submission_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("submission.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    verdict: Mapped[str] = mapped_column(String(50), nullable=False)
    effective_severity: Mapped[str] = mapped_column(String(20), nullable=False)
    findings: Mapped[Any] = mapped_column(JSONB, nullable=True)
    build_log_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sbom_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grype_report_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    defender_report_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    defender_timed_out: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DetectionFinding(Base):
    """Append-only: detection finding produced by the rule engine.

    Schema migration 0004 replaces the Phase 2 placeholder columns
    (kind, description) with the full rule-engine schema. See migration 0004.
    """
    __tablename__ = "detection_finding"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    submission_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("submission.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_version: Mapped[int] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    line_ranges: Mapped[Any] = mapped_column(JSONB, nullable=True)
    auto_fix: Mapped[Any] = mapped_column(JSONB, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class DetectionFindingDecision(Base):
    """Append-only: a researcher or admin decision on a detection finding.

    Design deviation: instead of UPDATE on detection_finding, decisions are
    stored in a separate append-only table. The UI LEFT JOINs on the most
    recent decision per finding. See docs/implementation-plans/...README.md
    "Approved design deviations".
    """
    __tablename__ = "detection_finding_decision"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    detection_finding_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("detection_finding.id", ondelete="RESTRICT", name="fk_dfd_finding_id"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(
        Enum("accept", "override", "auto_fix", "dismiss",
             name="detection_finding_decision_decision",
             native_enum=True,
             create_type=False),
        nullable=False,
    )
    decision_actor_principal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class ApprovalEvent(Base):
    """Append-only: approval decision or status change."""
    __tablename__ = "approval_event"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    submission_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("submission.id", ondelete="RESTRICT"),
        nullable=True,  # nullable since migration 0007: app-level events have no submission
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_principal_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class ReviewerToken(Base):
    """Long-lived token for approver access."""
    __tablename__ = "reviewer_token"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    principal_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    jti: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RevokedToken(Base):
    """Append-only: revoked token (audit)."""
    __tablename__ = "revoked_token"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    jti: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AccessLog(Base):
    """Append-only: access audit trail."""
    __tablename__ = "access_log"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    principal_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reviewer_token_jti: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("reviewer_token.jti", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    submission_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("submission.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class SigningKeyVersion(Base):
    """Signing key for token generation.

    Phase 5 adds app_slug to scope keys per app.
    """
    __tablename__ = "signing_key_version"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    app_slug: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kv_kid: Mapped[str | None] = mapped_column(String(512), nullable=True)
    kv_version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), default="ES256")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Agent(Base):
    """Service agent (for client-credentials auth)."""
    __tablename__ = "agent"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    entra_app_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    service_principal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    agent_metadata: Mapped[Any] = mapped_column("metadata", JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class WebhookSubscription(Base):
    """Webhook target for events."""
    __tablename__ = "webhook_subscription"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    callback_url: Mapped[str] = mapped_column(String(255), nullable=False)
    event_types: Mapped[Any] = mapped_column(JSONB, nullable=False)
    secret_name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    secret_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CostSnapshotMonthly(Base):
    """Monthly cost snapshot per app."""
    __tablename__ = "cost_snapshot_monthly"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    app_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    cost_usd: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("app_id", "year", "month", name="uq_cost_snapshot_app_ym"),
    )


class SharedReferenceCatalog(Base):
    """Shared reference libraries available to apps."""
    __tablename__ = "shared_reference_catalog"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    registry_url: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AppOwnershipFlag(Base):
    """Append-only: flag raised when the nightly Graph sweep finds a PI
    whose Entra account is deactivated or no longer exists.

    Design note: this mirrors the detection_finding / detection_finding_decision
    pattern — the flag row is never mutated; reviewer decisions are stored in
    AppOwnershipFlagReview.  Migration 0007 REVOKEs UPDATE/DELETE for rac_app.

    Verifies: rac-v1.AC9.2
    """
    __tablename__ = "app_ownership_flag"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    app_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    pi_principal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # "not_found" | "account_disabled"
    flagged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AppOwnershipFlagReview(Base):
    """Append-only: reviewer decision on an AppOwnershipFlag.

    The existence of a row here with flag_id=X means flag X has been reviewed.
    The sweep considers a flag "open" when no matching review row exists.

    Verifies: rac-v1.AC9.2, rac-v1.AC9.3 (transfer inserts a resolved_by_transfer review)
    """
    __tablename__ = "app_ownership_flag_review"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    flag_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_ownership_flag.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    review_decision: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # e.g. "resolved_by_transfer", "acknowledged", "transferred"
    reviewer_principal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdempotencyKey(Base):
    """Idempotency-Key storage for duplicate detection.

    Design deviation: not enumerated in design table list, but required
    for AC3.2 (idempotency across multi-replica ACA). Postgres-backed store
    replaces in-memory default from asgi-idempotency-header.
    """
    __tablename__ = "idempotency_key"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.text("uuidv7()"),
    )
    key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    principal_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    response_headers: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("key", "principal_id", name="uq_idempotency_key_principal"),
    )
