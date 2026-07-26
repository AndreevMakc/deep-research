"""Initial application schema.

Revision ID: 20260724_0001
Revises:
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260724_0001"
down_revision = None
branch_labels = None
depends_on = None


run_status = postgresql.ENUM(
    "CREATED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    name="run_status",
    create_type=False,
)
task_status = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    name="task_status",
    create_type=False,
)
claim_status = postgresql.ENUM(
    "UNVERIFIED",
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
    name="claim_status",
    create_type=False,
)
verification_verdict = postgresql.ENUM(
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "CONTRADICTED",
    "OUT_OF_SCOPE",
    "SOURCE_UNAVAILABLE",
    "CITATION_MISMATCH",
    "INSUFFICIENT_EVIDENCE",
    name="verification_verdict",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    run_status.create(bind, checkfirst=True)
    task_status.create(bind, checkfirst=True)
    claim_status.create(bind, checkfirst=True)
    verification_verdict.create(
        bind,
        checkfirst=True,
    )

    op.create_table(
        "research_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "status",
            run_status,
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

    op.create_table(
        "research_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "task_type",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "status",
            task_status,
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "assigned_agent",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "input_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "output_data",
            postgresql.JSONB(astext_type=sa.Text()),
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
    op.create_index(
        "ix_research_tasks_run_id",
        "research_tasks",
        ["run_id"],
    )

    op.create_table(
        "sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "canonical_url",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "publisher",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "local_path",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "canonical_url",
            name="uq_source_run_url",
        ),
    )
    op.create_index(
        "ix_sources_content_hash",
        "sources",
        ["content_hash"],
    )
    op.create_index(
        "ix_sources_run_id",
        "sources",
        ["run_id"],
    )

    op.create_table(
        "claims",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "evidence_quote",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "locator",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column(
            "status",
            claim_status,
            nullable=False,
        ),
        sa.Column(
            "created_by_agent",
            sa.String(length=100),
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
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_claims_run_id",
        "claims",
        ["run_id"],
    )
    op.create_index(
        "ix_claims_source_id",
        "claims",
        ["source_id"],
    )

    op.create_table(
        "verifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "verifier_agent",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "verdict",
            verification_verdict,
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "checked_source_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_verifications_claim_id",
        "verifications",
        ["claim_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verifications_claim_id",
        table_name="verifications",
    )
    op.drop_table("verifications")
    op.drop_index(
        "ix_claims_source_id",
        table_name="claims",
    )
    op.drop_index(
        "ix_claims_run_id",
        table_name="claims",
    )
    op.drop_table("claims")
    op.drop_index(
        "ix_sources_run_id",
        table_name="sources",
    )
    op.drop_index(
        "ix_sources_content_hash",
        table_name="sources",
    )
    op.drop_table("sources")
    op.drop_index(
        "ix_research_tasks_run_id",
        table_name="research_tasks",
    )
    op.drop_table("research_tasks")
    op.drop_table("research_runs")

    bind = op.get_bind()
    verification_verdict.drop(bind, checkfirst=True)
    claim_status.drop(bind, checkfirst=True)
    task_status.drop(bind, checkfirst=True)
    run_status.drop(bind, checkfirst=True)
