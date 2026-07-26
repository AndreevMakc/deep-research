"""Add immutable source snapshots and claim provenance.

Revision ID: 20260724_0002
Revises: 20260724_0001
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260724_0002"
down_revision = "20260724_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "final_url",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "local_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "http_status",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "content_length",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "retrieved_at",
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
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "content_hash",
            name="uq_source_snapshot_hash",
        ),
    )
    op.create_index(
        "ix_source_snapshots_content_hash",
        "source_snapshots",
        ["content_hash"],
    )
    op.create_index(
        "ix_source_snapshots_run_id",
        "source_snapshots",
        ["run_id"],
    )
    op.create_index(
        "ix_source_snapshots_source_id",
        "source_snapshots",
        ["source_id"],
    )

    op.alter_column(
        "sources",
        "retrieved_at",
        new_column_name="created_at",
    )

    op.execute(
        """
        INSERT INTO source_snapshots (
            id,
            source_id,
            run_id,
            final_url,
            content_hash,
            mime_type,
            local_path,
            http_status,
            content_length,
            metadata_json,
            retrieved_at
        )
        SELECT
            gen_random_uuid(),
            id,
            run_id,
            url,
            content_hash,
            COALESCE(mime_type, 'application/octet-stream'),
            local_path,
            NULL,
            NULL,
            metadata_json,
            created_at
        FROM sources
        WHERE content_hash IS NOT NULL
          AND local_path IS NOT NULL
        ON CONFLICT (source_id, content_hash) DO NOTHING
        """
    )

    op.add_column(
        "claims",
        sa.Column(
            "research_task_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "claims",
        sa.Column(
            "source_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "claims",
        sa.Column(
            "quote_start",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "claims",
        sa.Column(
            "quote_end",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_claims_research_task_id",
        "claims",
        "research_tasks",
        ["research_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_claims_source_snapshot_id",
        "claims",
        "source_snapshots",
        ["source_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_claims_research_task_id",
        "claims",
        ["research_task_id"],
    )
    op.create_index(
        "ix_claims_source_snapshot_id",
        "claims",
        ["source_snapshot_id"],
    )

    op.execute(
        """
        UPDATE claims AS claim
        SET source_snapshot_id = snapshot.id
        FROM source_snapshots AS snapshot
        WHERE snapshot.source_id = claim.source_id
        """
    )
    op.execute(
        """
        UPDATE claims AS claim
        SET research_task_id =
            (claim.locator->>'task_id')::uuid
        WHERE claim.locator ? 'task_id'
          AND claim.locator->>'task_id'
              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          AND EXISTS (
              SELECT 1
              FROM research_tasks AS task
              WHERE task.id =
                  (claim.locator->>'task_id')::uuid
          )
        """
    )

    op.drop_index(
        "ix_claims_source_id",
        table_name="claims",
    )
    op.drop_constraint(
        "claims_source_id_fkey",
        "claims",
        type_="foreignkey",
    )
    op.drop_column("claims", "source_id")

    op.drop_index(
        "ix_sources_content_hash",
        table_name="sources",
    )
    op.drop_column("sources", "content_hash")
    op.drop_column("sources", "mime_type")
    op.drop_column("sources", "local_path")
    op.drop_column("sources", "metadata_json")


def downgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "local_path",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "mime_type",
            sa.String(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_sources_content_hash",
        "sources",
        ["content_hash"],
    )

    op.execute(
        """
        UPDATE sources AS source
        SET
            content_hash = snapshot.content_hash,
            mime_type = snapshot.mime_type,
            local_path = snapshot.local_path,
            metadata_json = snapshot.metadata_json
        FROM (
            SELECT DISTINCT ON (source_id)
                source_id,
                content_hash,
                mime_type,
                local_path,
                metadata_json
            FROM source_snapshots
            ORDER BY source_id, retrieved_at DESC
        ) AS snapshot
        WHERE snapshot.source_id = source.id
        """
    )

    op.add_column(
        "claims",
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "claims_source_id_fkey",
        "claims",
        "sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_claims_source_id",
        "claims",
        ["source_id"],
    )
    op.execute(
        """
        UPDATE claims AS claim
        SET source_id = snapshot.source_id
        FROM source_snapshots AS snapshot
        WHERE snapshot.id = claim.source_snapshot_id
        """
    )

    op.drop_index(
        "ix_claims_source_snapshot_id",
        table_name="claims",
    )
    op.drop_index(
        "ix_claims_research_task_id",
        table_name="claims",
    )
    op.drop_constraint(
        "fk_claims_source_snapshot_id",
        "claims",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_claims_research_task_id",
        "claims",
        type_="foreignkey",
    )
    op.drop_column("claims", "quote_end")
    op.drop_column("claims", "quote_start")
    op.drop_column("claims", "source_snapshot_id")
    op.drop_column("claims", "research_task_id")

    op.alter_column(
        "sources",
        "created_at",
        new_column_name="retrieved_at",
    )

    op.drop_index(
        "ix_source_snapshots_source_id",
        table_name="source_snapshots",
    )
    op.drop_index(
        "ix_source_snapshots_run_id",
        table_name="source_snapshots",
    )
    op.drop_index(
        "ix_source_snapshots_content_hash",
        table_name="source_snapshots",
    )
    op.drop_table("source_snapshots")
