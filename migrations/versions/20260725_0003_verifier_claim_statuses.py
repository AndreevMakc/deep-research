"""Add verifier verdicts to claim status.

Revision ID: 20260725_0003
Revises: 20260724_0002
Create Date: 2026-07-25
"""

from alembic import op


revision = "20260725_0003"
down_revision = "20260724_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE claim_status "
        "ADD VALUE IF NOT EXISTS 'OUT_OF_SCOPE'"
    )
    op.execute(
        "ALTER TYPE claim_status "
        "ADD VALUE IF NOT EXISTS 'SOURCE_UNAVAILABLE'"
    )
    op.execute(
        "ALTER TYPE claim_status "
        "ADD VALUE IF NOT EXISTS 'CITATION_MISMATCH'"
    )


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum values while rows may use
    # them. Leaving the extra values is backward compatible with the
    # preceding application schema.
    pass
