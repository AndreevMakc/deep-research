"""Add optimistic revisions to research drafts.

Revision ID: 20260729_0013
Revises: 20260728_0012
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0013"
down_revision = "20260728_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_drafts",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_research_drafts_revision_positive",
        "research_drafts",
        "revision >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_research_drafts_revision_positive",
        "research_drafts",
        type_="check",
    )
    op.drop_column(
        "research_drafts",
        "revision",
    )
