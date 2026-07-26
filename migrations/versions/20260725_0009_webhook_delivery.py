"""Add signed webhook subscriptions and delivery queue.

Revision ID: 20260725_0009
Revises: 20260725_0008
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260725_0009"
down_revision = "20260725_0008"
branch_labels = None
depends_on = None


delivery_status = postgresql.ENUM(
    "PENDING",
    "DELIVERED",
    "FAILED",
    name="webhook_delivery_status",
    create_type=False,
)


def upgrade() -> None:
    delivery_status.create(
        op.get_bind(),
        checkfirst=True,
    )
    op.create_table(
        "webhook_subscriptions",
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
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "secret",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "events",
            postgresql.JSONB(astext_type=sa.Text()),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webhook_subscriptions_tenant_id",
        "webhook_subscriptions",
        ["tenant_id"],
    )
    op.create_table(
        "webhook_deliveries",
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
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            delivery_status,
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default="5",
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["webhook_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "event_id",
            name="uq_webhook_delivery_subscription_event",
        ),
    )
    for column in (
        "tenant_id",
        "subscription_id",
        "run_id",
        "event_type",
        "status",
        "next_attempt_at",
    ):
        op.create_index(
            f"ix_webhook_deliveries_{column}",
            "webhook_deliveries",
            [column],
        )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_index(
        "ix_webhook_subscriptions_tenant_id",
        table_name="webhook_subscriptions",
    )
    op.drop_table("webhook_subscriptions")
    delivery_status.drop(
        op.get_bind(),
        checkfirst=True,
    )
