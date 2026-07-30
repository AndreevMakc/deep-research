"""Add safe pause, resume, and early finish controls.

Revision ID: 20260730_0016
Revises: 20260729_0015
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0016"
down_revision = "20260729_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE run_status "
        "ADD VALUE IF NOT EXISTS 'PAUSE_REQUESTED'"
    )
    op.execute(
        "ALTER TYPE run_status "
        "ADD VALUE IF NOT EXISTS 'PAUSED'"
    )
    op.execute(
        "ALTER TYPE work_status "
        "ADD VALUE IF NOT EXISTS 'PAUSED'"
    )
    op.add_column(
        "work_items",
        sa.Column(
            "pause_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "work_items",
        sa.Column(
            "finish_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("work_items", "finish_requested")
    op.drop_column("work_items", "pause_requested")
    # PostgreSQL cannot safely remove enum values while rows may use
    # them. Leaving the values is backward compatible.
