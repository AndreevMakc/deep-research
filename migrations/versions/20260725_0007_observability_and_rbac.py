"""Add operational telemetry and reviewer RBAC.

Revision ID: 20260725_0007
Revises: 20260725_0006
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260725_0007"
down_revision = "20260725_0006"
branch_labels = None
depends_on = None


reviewer_role = postgresql.ENUM(
    "VIEWER",
    "REVIEWER",
    "PUBLISHER",
    "ADMIN",
    name="reviewer_role",
    create_type=False,
)
event_status = postgresql.ENUM(
    "STARTED",
    "SUCCEEDED",
    "FAILED",
    "RETRYING",
    "INFO",
    name="event_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    reviewer_role.create(bind, checkfirst=True)
    event_status.create(bind, checkfirst=True)

    op.create_table(
        "reviewer_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "subject",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "role",
            reviewer_role,
            nullable=False,
        ),
        sa.Column(
            "active",
            sa.Boolean(),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reviewer_identities_subject",
        "reviewer_identities",
        ["subject"],
        unique=True,
    )
    op.add_column(
        "review_decisions",
        sa.Column(
            "reviewer_identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_review_decisions_reviewer_identity",
        "review_decisions",
        "reviewer_identities",
        ["reviewer_identity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_review_decisions_reviewer_identity_id",
        "review_decisions",
        ["reviewer_identity_id"],
    )

    op.create_table(
        "operational_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "correlation_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "agent",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "operation",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "status",
            event_status,
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column(
            "duration_ms",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "token_estimate",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Float(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "error_code",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
    for column in (
        "run_id",
        "correlation_id",
        "task_id",
        "claim_id",
        "agent",
        "operation",
        "event_type",
        "error_code",
        "created_at",
    ):
        op.create_index(
            f"ix_operational_events_{column}",
            "operational_events",
            [column],
        )


def downgrade() -> None:
    op.drop_table("operational_events")
    op.drop_index(
        "ix_review_decisions_reviewer_identity_id",
        table_name="review_decisions",
    )
    op.drop_constraint(
        "fk_review_decisions_reviewer_identity",
        "review_decisions",
        type_="foreignkey",
    )
    op.drop_column(
        "review_decisions",
        "reviewer_identity_id",
    )
    op.drop_index(
        "ix_reviewer_identities_subject",
        table_name="reviewer_identities",
    )
    op.drop_table("reviewer_identities")

    bind = op.get_bind()
    event_status.drop(bind, checkfirst=True)
    reviewer_role.drop(bind, checkfirst=True)
