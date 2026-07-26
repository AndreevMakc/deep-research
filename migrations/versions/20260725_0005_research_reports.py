"""Add persisted final research reports.

Revision ID: 20260725_0005
Revises: 20260725_0004
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260725_0005"
down_revision = "20260725_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_reports",
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
            "markdown_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "json_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "markdown_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "json_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
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
            ["run_id"],
            ["research_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            name="uq_research_report_run",
        ),
    )
    op.create_index(
        "ix_research_reports_run_id",
        "research_reports",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_reports_run_id",
        table_name="research_reports",
    )
    op.drop_table("research_reports")
