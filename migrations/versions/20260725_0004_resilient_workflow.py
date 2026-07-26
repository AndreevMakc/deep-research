"""Add resilient workflow status and verification idempotency.

Revision ID: 20260725_0004
Revises: 20260725_0003
Create Date: 2026-07-25
"""

from alembic import op


revision = "20260725_0004"
down_revision = "20260725_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE run_status "
        "ADD VALUE IF NOT EXISTS 'COMPLETED_WITH_ERRORS'"
    )
    op.execute(
        """
        DELETE FROM verifications AS older
        USING verifications AS newer
        WHERE older.claim_id = newer.claim_id
          AND older.verifier_agent = newer.verifier_agent
          AND (
              older.created_at < newer.created_at
              OR (
                  older.created_at = newer.created_at
                  AND older.id::text < newer.id::text
              )
          )
        """
    )
    op.create_unique_constraint(
        "uq_verification_claim_agent",
        "verifications",
        ["claim_id", "verifier_agent"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_verification_claim_agent",
        "verifications",
        type_="unique",
    )
    # PostgreSQL enum values cannot be removed safely while existing
    # rows may reference them. The extra value is backward compatible.
