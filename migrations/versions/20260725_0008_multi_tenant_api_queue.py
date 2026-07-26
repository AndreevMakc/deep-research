"""Add multi-tenant API identities and durable work queue.

Revision ID: 20260725_0008
Revises: 20260725_0007
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260725_0008"
down_revision = "20260725_0007"
branch_labels = None
depends_on = None


api_role = postgresql.ENUM(
    "VIEWER",
    "RESEARCHER",
    "REVIEWER",
    "PUBLISHER",
    "ADMIN",
    name="api_role",
    create_type=False,
)
work_status = postgresql.ENUM(
    "QUEUED",
    "LEASED",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="work_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        "ALTER TYPE run_status "
        "ADD VALUE IF NOT EXISTS 'CANCEL_REQUESTED'"
    )
    op.execute(
        "ALTER TYPE run_status "
        "ADD VALUE IF NOT EXISTS 'CANCELLED'"
    )
    bind = op.get_bind()
    api_role.create(bind, checkfirst=True)
    work_status.create(bind, checkfirst=True)

    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "slug",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tenants_slug",
        "tenants",
        ["slug"],
        unique=True,
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_research_runs_tenant",
        "research_runs",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_research_runs_tenant_id",
        "research_runs",
        ["tenant_id"],
    )

    op.create_table(
        "api_identities",
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
            "subject",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "role",
            api_role,
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(length=64),
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
        sa.UniqueConstraint(
            "tenant_id",
            "subject",
            name="uq_api_identity_tenant_subject",
        ),
    )
    op.create_index(
        "ix_api_identities_tenant_id",
        "api_identities",
        ["tenant_id"],
    )
    op.create_index(
        "ix_api_identities_token_hash",
        "api_identities",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "work_items",
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
            "kind",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "status",
            work_status,
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "lease_owner",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
            server_default="3",
            nullable=False,
        ),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "kind",
            name="uq_work_item_run_kind",
        ),
    )
    for column in (
        "tenant_id",
        "run_id",
        "status",
        "lease_owner",
        "lease_expires_at",
        "available_at",
    ):
        op.create_index(
            f"ix_work_items_{column}",
            "work_items",
            [column],
        )

    op.create_table(
        "idempotency_records",
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
            "identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "request_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "response_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status_code",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["api_identities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "key",
            name="uq_idempotency_tenant_key",
        ),
    )
    op.create_index(
        "ix_idempotency_records_tenant_id",
        "idempotency_records",
        ["tenant_id"],
    )
    op.create_index(
        "ix_idempotency_records_identity_id",
        "idempotency_records",
        ["identity_id"],
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("work_items")
    op.drop_index(
        "ix_api_identities_token_hash",
        table_name="api_identities",
    )
    op.drop_index(
        "ix_api_identities_tenant_id",
        table_name="api_identities",
    )
    op.drop_table("api_identities")
    op.drop_index(
        "ix_research_runs_tenant_id",
        table_name="research_runs",
    )
    op.drop_constraint(
        "fk_research_runs_tenant",
        "research_runs",
        type_="foreignkey",
    )
    op.drop_column("research_runs", "tenant_id")
    op.drop_index(
        "ix_tenants_slug",
        table_name="tenants",
    )
    op.drop_table("tenants")

    bind = op.get_bind()
    work_status.drop(bind, checkfirst=True)
    api_role.drop(bind, checkfirst=True)
    # PostgreSQL enum values added to run_status are intentionally
    # retained because removing them is unsafe while rows may use them.
