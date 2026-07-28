from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RunStatus(str, enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class ResearchDraftStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ClaimStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    OUT_OF_SCOPE = "out_of_scope"
    SOURCE_UNAVAILABLE = "source_unavailable"
    CITATION_MISMATCH = "citation_mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class VerificationVerdict(str, enum.Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    OUT_OF_SCOPE = "out_of_scope"
    SOURCE_UNAVAILABLE = "source_unavailable"
    CITATION_MISMATCH = "citation_mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ClaimReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESEARCH_REQUESTED = "research_requested"


class ReportReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class ReviewTargetType(str, enum.Enum):
    CLAIM = "claim"
    REPORT = "report"


class ReviewDecisionType(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_RESEARCH = "request_research"
    PUBLISH = "publish"


class ReviewerRole(str, enum.Enum):
    VIEWER = "viewer"
    REVIEWER = "reviewer"
    PUBLISHER = "publisher"
    ADMIN = "admin"


class EventStatus(str, enum.Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    INFO = "info"


class ApiRole(str, enum.Enum):
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    REVIEWER = "reviewer"
    PUBLISHER = "publisher"
    ADMIN = "admin"


class WorkStatus(str, enum.Enum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WebhookDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    created_by_identity_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "api_identities.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )

    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"),
        nullable=False,
        default=RunStatus.CREATED,
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

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    max_external_requests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )

    max_sources: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
    )

    max_claims: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )

    max_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=200_000,
    )

    max_run_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3_600,
    )

    external_requests_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    tokens_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    tasks: Mapped[list["ResearchTask"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    sources: Mapped[list["Source"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    source_snapshots: Mapped[
        list["SourceSnapshot"]
    ] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    claims: Mapped[list["Claim"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    report: Mapped["ResearchReport | None"] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )

    review_decisions: Mapped[
        list["ReviewDecision"]
    ] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    operational_events: Mapped[
        list["OperationalEvent"]
    ] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    tenant: Mapped["Tenant | None"] = relationship(
        back_populates="runs"
    )

    created_by: Mapped["ApiIdentity | None"] = relationship(
        back_populates="created_runs"
    )

    work_items: Mapped[list["WorkItem"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    views: Mapped[list["ResearchRunView"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class ResearchDraft(Base):
    __tablename__ = "research_drafts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_research_draft_run",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "api_identities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    scope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    period: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    assumptions: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    estimated_duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[ResearchDraftStatus] = mapped_column(
        Enum(
            ResearchDraftStatus,
            name="research_draft_status",
        ),
        nullable=False,
        default=ResearchDraftStatus.DRAFT,
        index=True,
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
        index=True,
    )


class ResearchRunView(Base):
    __tablename__ = "research_run_views"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "identity_id",
            name="uq_research_run_view_run_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "api_identities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    result_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="views"
    )


class ResearchTask(Base):
    __tablename__ = "research_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"),
        nullable=False,
        default=TaskStatus.PENDING,
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )

    assigned_agent: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    input_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    output_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="tasks"
    )

    claims: Mapped[list["Claim"]] = relationship(
        back_populates="research_task"
    )


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "canonical_url",
            name="uq_source_run_url",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    canonical_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    publisher: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="sources"
    )

    snapshots: Mapped[list["SourceSnapshot"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "content_hash",
            name="uq_source_snapshot_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    final_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    local_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    http_status: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    content_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source: Mapped["Source"] = relationship(
        back_populates="snapshots"
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="source_snapshots"
    )

    claims: Mapped[list["Claim"]] = relationship(
        back_populates="source_snapshot"
    )


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    research_task_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "research_tasks.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    source_snapshot_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "source_snapshots.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    evidence_quote: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    quote_start: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    quote_end: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    locator: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    scope: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, name="claim_status"),
        nullable=False,
        default=ClaimStatus.UNVERIFIED,
    )

    review_status: Mapped[ClaimReviewStatus] = mapped_column(
        Enum(
            ClaimReviewStatus,
            name="claim_review_status",
        ),
        nullable=False,
        default=ClaimReviewStatus.PENDING,
    )

    created_by_agent: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="claims"
    )

    research_task: Mapped[
        "ResearchTask | None"
    ] = relationship(
        back_populates="claims"
    )

    source_snapshot: Mapped[
        "SourceSnapshot | None"
    ] = relationship(
        back_populates="claims"
    )

    verifications: Mapped[list["Verification"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
    )


class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "verifier_agent",
            name="uq_verification_claim_agent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    verifier_agent: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    verdict: Mapped[VerificationVerdict] = mapped_column(
        Enum(
            VerificationVerdict,
            name="verification_verdict",
        ),
        nullable=False,
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    checked_source_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    claim: Mapped["Claim"] = relationship(
        back_populates="verifications"
    )


class ResearchReport(Base):
    __tablename__ = "research_reports"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_research_report_run",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    markdown_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    json_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    markdown_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    json_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    result_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    review_status: Mapped[ReportReviewStatus] = mapped_column(
        Enum(
            ReportReviewStatus,
            name="report_review_status",
        ),
        nullable=False,
        default=ReportReviewStatus.PENDING,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    published_at: Mapped[datetime | None] = mapped_column(
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
        onupdate=func.now(),
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="report"
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    target_type: Mapped[ReviewTargetType] = mapped_column(
        Enum(
            ReviewTargetType,
            name="review_target_type",
        ),
        nullable=False,
    )

    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    decision: Mapped[ReviewDecisionType] = mapped_column(
        Enum(
            ReviewDecisionType,
            name="review_decision_type",
        ),
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    reviewer: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    reviewer_identity_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        ForeignKey(
            "reviewer_identities.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="review_decisions"
    )

    reviewer_identity: Mapped[
        "ReviewerIdentity | None"
    ] = relationship(
        back_populates="decisions"
    )


class ReviewerIdentity(Base):
    __tablename__ = "reviewer_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[ReviewerRole] = mapped_column(
        Enum(ReviewerRole, name="reviewer_role"),
        nullable=False,
        default=ReviewerRole.VIEWER,
    )

    active: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
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

    decisions: Mapped[
        list["ReviewDecision"]
    ] = relationship(
        back_populates="reviewer_identity"
    )


class OperationalEvent(Base):
    __tablename__ = "operational_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    correlation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    agent: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    operation: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status"),
        nullable=False,
    )

    attempt: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    token_estimate: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    estimated_cost_usd: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    run: Mapped["ResearchRun | None"] = relationship(
        back_populates="operational_events"
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    runs: Mapped[list["ResearchRun"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    research_drafts: Mapped[
        list["ResearchDraft"]
    ] = relationship(
        cascade="all, delete-orphan",
    )

    api_identities: Mapped[
        list["ApiIdentity"]
    ] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    work_items: Mapped[list["WorkItem"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    webhook_subscriptions: Mapped[
        list["WebhookSubscription"]
    ] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )


class ApiIdentity(Base):
    __tablename__ = "api_identities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject",
            name="uq_api_identity_tenant_subject",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[ApiRole] = mapped_column(
        Enum(ApiRole, name="api_role"),
        nullable=False,
    )

    token_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
    )

    password_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    active: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    tenant: Mapped["Tenant"] = relationship(
        back_populates="api_identities"
    )

    idempotency_records: Mapped[
        list["IdempotencyRecord"]
    ] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
    )

    sessions: Mapped[list["BrowserSession"]] = relationship(
        back_populates="identity",
        cascade="all, delete-orphan",
    )

    created_runs: Mapped[list["ResearchRun"]] = relationship(
        back_populates="created_by",
    )

    created_research_drafts: Mapped[
        list["ResearchDraft"]
    ] = relationship(
        cascade="all, delete-orphan",
    )


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "api_identities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    identity: Mapped["ApiIdentity"] = relationship(
        back_populates="sessions"
    )


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "kind",
            name="uq_work_item_run_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[WorkStatus] = mapped_column(
        Enum(WorkStatus, name="work_status"),
        nullable=False,
        default=WorkStatus.QUEUED,
        index=True,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    lease_owner: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )

    cancel_requested: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
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
        onupdate=func.now(),
    )

    tenant: Mapped["Tenant"] = relationship(
        back_populates="work_items"
    )

    run: Mapped["ResearchRun"] = relationship(
        back_populates="work_items"
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "key",
            name="uq_idempotency_tenant_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "api_identities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    response_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    status_code: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    identity: Mapped["ApiIdentity"] = relationship(
        back_populates="idempotency_records"
    )


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    secret: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    events: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    active: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    tenant: Mapped["Tenant"] = relationship(
        back_populates="webhook_subscriptions"
    )

    deliveries: Mapped[
        list["WebhookDelivery"]
    ] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "event_id",
            name="uq_webhook_delivery_subscription_event",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "webhook_subscriptions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    status: Mapped[
        WebhookDeliveryStatus
    ] = mapped_column(
        Enum(
            WebhookDeliveryStatus,
            name="webhook_delivery_status",
        ),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        index=True,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
    )

    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    subscription: Mapped[
        "WebhookSubscription"
    ] = relationship(
        back_populates="deliveries"
    )
