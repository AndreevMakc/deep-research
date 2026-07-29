"""Add research input materials and settings.

Revision ID: 20260729_0015
Revises: 20260729_0014
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260729_0015"
down_revision = "20260729_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_drafts",
        sa.Column(
            "auto_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "research_drafts",
        sa.Column(
            "settings_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "research_draft_materials",
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
            "draft_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(length=30),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "url",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "text_content",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "byte_size",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "storage_path",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "kind IN "
                "('url', 'pdf', 'text', 'markdown', 'note')"
            ),
            name="ck_research_draft_material_kind",
        ),
        sa.CheckConstraint(
            (
                "role IN "
                "('verify', 'primary_source', "
                "'context_only', 'do_not_cite')"
            ),
            name="ck_research_draft_material_role",
        ),
        sa.CheckConstraint(
            "byte_size >= 0",
            name="ck_research_draft_material_size_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["research_drafts.id"],
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
        "ix_research_draft_materials_tenant_id",
        "research_draft_materials",
        ["tenant_id"],
    )
    op.create_index(
        "ix_research_draft_materials_draft_id",
        "research_draft_materials",
        ["draft_id"],
    )
    op.create_index(
        "ix_research_draft_materials_content_hash",
        "research_draft_materials",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_table("research_draft_materials")
    op.drop_column(
        "research_drafts",
        "settings_overrides",
    )
    op.drop_column(
        "research_drafts",
        "auto_settings",
    )
