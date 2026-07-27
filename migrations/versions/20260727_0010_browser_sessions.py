"""Add password accounts, browser sessions, and run authors.

Revision ID: 20260727_0010
Revises: 20260725_0009
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260727_0010"
down_revision = "20260725_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "api_identities",
        "token_hash",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.add_column(
        "api_identities",
        sa.Column(
            "password_hash",
            sa.Text(),
            nullable=True,
        ),
    )
    op.create_table(
        "browser_sessions",
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
            "token_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
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
    )
    op.create_index(
        "ix_browser_sessions_tenant_id",
        "browser_sessions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_browser_sessions_identity_id",
        "browser_sessions",
        ["identity_id"],
    )
    op.create_index(
        "ix_browser_sessions_token_hash",
        "browser_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_browser_sessions_expires_at",
        "browser_sessions",
        ["expires_at"],
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "created_by_identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_research_runs_created_by_identity",
        "research_runs",
        "api_identities",
        ["created_by_identity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_research_runs_created_by_identity_id",
        "research_runs",
        ["created_by_identity_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_runs_created_by_identity_id",
        table_name="research_runs",
    )
    op.drop_constraint(
        "fk_research_runs_created_by_identity",
        "research_runs",
        type_="foreignkey",
    )
    op.drop_column(
        "research_runs",
        "created_by_identity_id",
    )
    op.drop_table("browser_sessions")
    op.drop_column("api_identities", "password_hash")
    op.execute(
        """
        UPDATE api_identities
        SET token_hash =
            md5(id::text) || md5(id::text || '-downgrade')
        WHERE token_hash IS NULL
        """
    )
    op.alter_column(
        "api_identities",
        "token_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )
