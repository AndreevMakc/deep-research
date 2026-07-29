"""Add persisted research draft clarifications.

Revision ID: 20260729_0014
Revises: 20260729_0013
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260729_0014"
down_revision = "20260729_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_drafts",
        sa.Column(
            "requires_clarification",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "research_drafts",
        sa.Column(
            "clarification_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "research_drafts",
        sa.Column(
            "clarification_answers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "research_drafts",
        sa.Column(
            "clarification_index",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_research_drafts_clarification_index_nonnegative",
        "research_drafts",
        "clarification_index >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_research_drafts_clarification_index_nonnegative",
        "research_drafts",
        type_="check",
    )
    op.drop_column(
        "research_drafts",
        "clarification_index",
    )
    op.drop_column(
        "research_drafts",
        "clarification_answers",
    )
    op.drop_column(
        "research_drafts",
        "clarification_questions",
    )
    op.drop_column(
        "research_drafts",
        "requires_clarification",
    )
