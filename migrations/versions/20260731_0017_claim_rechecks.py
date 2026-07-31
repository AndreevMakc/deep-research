"""Add claim recheck audit trail and report versions.

Revision ID: 20260731_0017
Revises: 20260730_0016
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260731_0017"
down_revision = "20260730_0016"
branch_labels = None
depends_on = None


claim_recheck_category = postgresql.ENUM(
    "EVIDENCE_INCORRECT",
    "SOURCE_OUTDATED",
    "SOURCE_UNAVAILABLE",
    "OTHER",
    name="claim_recheck_category",
    create_type=False,
)
claim_recheck_status = postgresql.ENUM(
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    name="claim_recheck_status",
    create_type=False,
)
verification_verdict = postgresql.ENUM(
    name="verification_verdict",
    create_type=False,
)


def upgrade() -> None:
    claim_recheck_category.create(
        op.get_bind(),
        checkfirst=True,
    )
    claim_recheck_status.create(
        op.get_bind(),
        checkfirst=True,
    )
    op.create_table(
        "claim_recheck_requests",
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
            "claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "requested_by_identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column(
            "category",
            claim_recheck_category,
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "status",
            claim_recheck_status,
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column(
            "original_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "result_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "original_verdict",
            verification_verdict,
            nullable=True,
        ),
        sa.Column(
            "result_verdict",
            verification_verdict,
            nullable=True,
        ),
        sa.Column(
            "material_changed",
            sa.Boolean(),
            nullable=True,
        ),
        sa.Column(
            "report_version_number",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_identity_id"],
            ["api_identities.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["original_snapshot_id"],
            ["source_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["result_snapshot_id"],
            ["source_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "tenant_id",
        "run_id",
        "claim_id",
        "requested_by_identity_id",
        "status",
    ):
        op.create_index(
            f"ix_claim_recheck_requests_{column}",
            "claim_recheck_requests",
            [column],
        )

    op.create_table(
        "research_report_versions",
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
            "claim_recheck_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "version_number",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "requested_by",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "markdown_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "json_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "result_json",
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
        sa.ForeignKeyConstraint(
            ["claim_recheck_id"],
            ["claim_recheck_requests.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "version_number",
            name="uq_research_report_version_run_number",
        ),
    )
    op.create_index(
        "ix_research_report_versions_run_id",
        "research_report_versions",
        ["run_id"],
    )
    op.create_index(
        "ix_research_report_versions_claim_recheck_id",
        "research_report_versions",
        ["claim_recheck_id"],
    )


def downgrade() -> None:
    op.drop_table("research_report_versions")
    op.drop_table("claim_recheck_requests")
    claim_recheck_status.drop(
        op.get_bind(),
        checkfirst=True,
    )
    claim_recheck_category.drop(
        op.get_bind(),
        checkfirst=True,
    )
