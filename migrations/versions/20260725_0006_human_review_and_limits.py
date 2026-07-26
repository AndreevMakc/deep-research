"""Add human review, publication gate, and run limits.

Revision ID: 20260725_0006
Revises: 20260725_0005
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260725_0006"
down_revision = "20260725_0005"
branch_labels = None
depends_on = None


claim_review_status = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "RESEARCH_REQUESTED",
    name="claim_review_status",
    create_type=False,
)
report_review_status = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "PUBLISHED",
    name="report_review_status",
    create_type=False,
)
review_target_type = postgresql.ENUM(
    "CLAIM",
    "REPORT",
    name="review_target_type",
    create_type=False,
)
review_decision_type = postgresql.ENUM(
    "APPROVE",
    "REJECT",
    "REQUEST_RESEARCH",
    "PUBLISH",
    name="review_decision_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    claim_review_status.create(bind, checkfirst=True)
    report_review_status.create(bind, checkfirst=True)
    review_target_type.create(bind, checkfirst=True)
    review_decision_type.create(bind, checkfirst=True)

    op.add_column(
        "research_runs",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    for name, default in (
        ("max_external_requests", 100),
        ("max_sources", 50),
        ("max_claims", 100),
        ("max_tokens", 200_000),
        ("max_run_seconds", 3_600),
        ("external_requests_used", 0),
        ("tokens_used", 0),
    ):
        op.add_column(
            "research_runs",
            sa.Column(
                name,
                sa.Integer(),
                server_default=str(default),
                nullable=False,
            ),
        )

    op.add_column(
        "claims",
        sa.Column(
            "review_status",
            claim_review_status,
            server_default="PENDING",
            nullable=False,
        ),
    )
    op.add_column(
        "research_reports",
        sa.Column(
            "review_status",
            report_review_status,
            server_default="PENDING",
            nullable=False,
        ),
    )
    op.add_column(
        "research_reports",
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "research_reports",
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "review_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "target_type",
            review_target_type,
            nullable=False,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "decision",
            review_decision_type,
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "reviewer",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_decisions_run_id",
        "review_decisions",
        ["run_id"],
    )
    op.create_index(
        "ix_review_decisions_target_id",
        "review_decisions",
        ["target_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_decisions_target_id",
        table_name="review_decisions",
    )
    op.drop_index(
        "ix_review_decisions_run_id",
        table_name="review_decisions",
    )
    op.drop_table("review_decisions")
    op.drop_column("research_reports", "published_at")
    op.drop_column("research_reports", "approved_at")
    op.drop_column("research_reports", "review_status")
    op.drop_column("claims", "review_status")

    for name in (
        "tokens_used",
        "external_requests_used",
        "max_run_seconds",
        "max_tokens",
        "max_claims",
        "max_sources",
        "max_external_requests",
        "started_at",
    ):
        op.drop_column("research_runs", name)

    bind = op.get_bind()
    review_decision_type.drop(bind, checkfirst=True)
    review_target_type.drop(bind, checkfirst=True)
    report_review_status.drop(bind, checkfirst=True)
    claim_review_status.drop(bind, checkfirst=True)
