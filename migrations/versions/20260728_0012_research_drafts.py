"""Add persisted research drafts.

Revision ID: 20260728_0012
Revises: 20260727_0011
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0012"
down_revision = "20260727_0011"
branch_labels = None
depends_on = None


research_draft_status = postgresql.ENUM(
    "DRAFT",
    "CONFIRMED",
    name="research_draft_status",
    create_type=False,
)


def upgrade() -> None:
    research_draft_status.create(
        op.get_bind(),
        checkfirst=True,
    )
    op.create_table(
        "research_drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_by_identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "question",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "period",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "estimated_duration_minutes",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "status",
            research_draft_status,
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"],
            ["api_identities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            name="uq_research_draft_run",
        ),
    )
    op.create_index(
        "ix_research_drafts_tenant_id",
        "research_drafts",
        ["tenant_id"],
    )
    op.create_index(
        "ix_research_drafts_created_by_identity_id",
        "research_drafts",
        ["created_by_identity_id"],
    )
    op.create_index(
        "ix_research_drafts_run_id",
        "research_drafts",
        ["run_id"],
    )
    op.create_index(
        "ix_research_drafts_status",
        "research_drafts",
        ["status"],
    )
    op.create_index(
        "ix_research_drafts_updated_at",
        "research_drafts",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_table("research_drafts")
    research_draft_status.drop(
        op.get_bind(),
        checkfirst=True,
    )
