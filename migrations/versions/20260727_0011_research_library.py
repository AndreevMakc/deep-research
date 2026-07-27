"""Add research library metadata and per-user read state.

Revision ID: 20260727_0011
Revises: 20260727_0010
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260727_0011"
down_revision = "20260727_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column(
            "title",
            sa.String(length=160),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE research_runs
        SET title = coalesce(
            nullif(
                left(
                    regexp_replace(
                        trim(question),
                        '\\s+',
                        ' ',
                        'g'
                    ),
                    160
                ),
                ''
            ),
            'Исследование'
        )
        """
    )
    op.alter_column(
        "research_runs",
        "title",
        existing_type=sa.String(length=160),
        nullable=False,
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_research_runs_archived_at",
        "research_runs",
        ["archived_at"],
    )
    op.create_table(
        "research_run_views",
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
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "result_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["api_identities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "identity_id",
            name="uq_research_run_view_run_identity",
        ),
    )
    op.create_index(
        "ix_research_run_views_tenant_id",
        "research_run_views",
        ["tenant_id"],
    )
    op.create_index(
        "ix_research_run_views_run_id",
        "research_run_views",
        ["run_id"],
    )
    op.create_index(
        "ix_research_run_views_identity_id",
        "research_run_views",
        ["identity_id"],
    )


def downgrade() -> None:
    op.drop_table("research_run_views")
    op.drop_index(
        "ix_research_runs_archived_at",
        table_name="research_runs",
    )
    op.drop_column("research_runs", "archived_at")
    op.drop_column("research_runs", "title")
