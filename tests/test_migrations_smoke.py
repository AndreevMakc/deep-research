import os
import uuid

import psycopg
from psycopg import sql
from sqlalchemy import (
    inspect,
    make_url,
    text,
)


def main() -> None:
    original_url = os.environ.get("DATABASE_URL")

    if not original_url:
        from app.config import get_settings

        original_url = get_settings().database_url

    database_url = make_url(
        original_url
    )
    database_name = (
        "deep_research_migration_"
        + uuid.uuid4().hex[:12]
    )
    admin_conninfo = database_url.set(
        database="postgres",
    ).render_as_string(
        hide_password=False
    )
    test_conninfo = database_url.set(
        database=database_name,
    ).render_as_string(
        hide_password=False
    )

    with psycopg.connect(
        admin_conninfo,
        autocommit=True,
    ) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(database_name)
            )
        )

    try:
        os.environ["DATABASE_URL"] = test_conninfo

        from alembic import command

        from app.config import get_settings

        get_settings.cache_clear()

        from app.db.migrate import (
            alembic_config,
            upgrade_database,
        )
        from app.db.session import engine

        if engine.url.database != database_name:
            raise RuntimeError(
                "Migration smoke test refused to run "
                "outside its temporary database"
            )

        upgrade_database()

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        claim_columns = {
            column["name"]
            for column in inspector.get_columns(
                "claims"
            )
        }
        source_columns = {
            column["name"]
            for column in inspector.get_columns(
                "sources"
            )
        }

        assert "source_snapshots" in tables
        assert "research_reports" in tables
        assert "review_decisions" in tables
        assert "reviewer_identities" in tables
        assert "operational_events" in tables
        assert "tenants" in tables
        assert "api_identities" in tables
        assert "browser_sessions" in tables
        assert "research_run_views" in tables
        assert "research_drafts" in tables
        assert "research_draft_materials" in tables
        assert "claim_recheck_requests" in tables
        assert "research_report_versions" in tables
        assert "user_notifications" in tables
        assert "work_items" in tables
        assert "idempotency_records" in tables
        assert "webhook_subscriptions" in tables
        assert "webhook_deliveries" in tables
        assert "source_snapshot_id" in claim_columns
        assert "research_task_id" in claim_columns
        assert "quote_start" in claim_columns
        assert "quote_end" in claim_columns
        assert "content_hash" not in source_columns
        run_columns = {
            column["name"]
            for column in inspector.get_columns(
                "research_runs"
            )
        }
        identity_columns = {
            column["name"]
            for column in inspector.get_columns(
                "api_identities"
            )
        }
        report_columns = {
            column["name"]
            for column in inspector.get_columns(
                "research_reports"
            )
        }
        draft_columns = {
            column["name"]
            for column in inspector.get_columns(
                "research_drafts"
            )
        }
        assert {
            "started_at",
            "created_by_identity_id",
            "title",
            "archived_at",
            "max_external_requests",
            "max_sources",
            "max_claims",
            "max_tokens",
            "max_run_seconds",
            "external_requests_used",
            "tokens_used",
        } <= run_columns
        assert "password_hash" in identity_columns
        assert {
            "review_status",
            "approved_at",
            "published_at",
        } <= report_columns
        assert {
            "tenant_id",
            "created_by_identity_id",
            "run_id",
            "question",
            "scope",
            "period",
            "assumptions",
            "estimated_duration_minutes",
            "requires_clarification",
            "clarification_questions",
            "clarification_answers",
            "clarification_index",
            "revision",
            "status",
            "created_at",
            "updated_at",
            "auto_settings",
            "settings_overrides",
        } <= draft_columns
        material_columns = {
            column["name"]
            for column in inspector.get_columns(
                "research_draft_materials"
            )
        }
        assert {
            "tenant_id",
            "draft_id",
            "kind",
            "role",
            "name",
            "url",
            "text_content",
            "mime_type",
            "content_hash",
            "byte_size",
            "storage_path",
            "created_at",
        } <= material_columns
        work_columns = {
            column["name"]
            for column in inspector.get_columns(
                "work_items"
            )
        }
        assert {
            "pause_requested",
            "finish_requested",
        } <= work_columns
        draft_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "research_drafts"
            )
        }
        assert (
            "ck_research_drafts_revision_positive"
            in draft_checks
        )
        assert (
            "ck_research_drafts_"
            "clarification_index_nonnegative"
            in draft_checks
        )
        assert "review_status" in claim_columns
        review_columns = {
            column["name"]
            for column in inspector.get_columns(
                "review_decisions"
            )
        }
        assert "reviewer_identity_id" in review_columns

        with engine.connect() as connection:
            claim_statuses = set(
                connection.execute(
                    text(
                        """
                        SELECT enumlabel
                        FROM pg_enum
                        JOIN pg_type
                          ON pg_type.oid =
                             pg_enum.enumtypid
                        WHERE pg_type.typname =
                              'claim_status'
                        """
                    )
                ).scalars()
            )

        assert {
            "OUT_OF_SCOPE",
            "SOURCE_UNAVAILABLE",
            "CITATION_MISMATCH",
        } <= claim_statuses

        with engine.connect() as connection:
            run_statuses = set(
                connection.execute(
                    text(
                        """
                        SELECT enumlabel
                        FROM pg_enum
                        JOIN pg_type
                          ON pg_type.oid =
                             pg_enum.enumtypid
                        WHERE pg_type.typname =
                              'run_status'
                        """
                    )
                ).scalars()
            )

        assert {
            "COMPLETED_WITH_ERRORS",
            "PAUSE_REQUESTED",
            "PAUSED",
        } <= run_statuses
        with engine.connect() as connection:
            work_statuses = set(
                connection.execute(
                    text(
                        """
                        SELECT enumlabel
                        FROM pg_enum
                        JOIN pg_type
                          ON pg_type.oid =
                             pg_enum.enumtypid
                        WHERE pg_type.typname =
                              'work_status'
                        """
                    )
                ).scalars()
            )
        assert "PAUSED" in work_statuses
        verification_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "verifications"
            )
        }
        assert (
            "uq_verification_claim_agent"
            in verification_constraints
        )

        command.downgrade(
            alembic_config(),
            "base",
        )
        engine.dispose()

        remaining_tables = set(
            inspect(engine).get_table_names()
        )
        assert not (
            {
                "research_runs",
                "research_tasks",
                "sources",
                "source_snapshots",
                "claims",
                "verifications",
                "research_reports",
                "review_decisions",
                "reviewer_identities",
                "operational_events",
                "tenants",
                "api_identities",
                "browser_sessions",
                "research_run_views",
                "research_drafts",
                "claim_recheck_requests",
                "research_report_versions",
                "user_notifications",
                "work_items",
                "idempotency_records",
                "webhook_subscriptions",
                "webhook_deliveries",
            }
            & remaining_tables
        )

        command.upgrade(
            alembic_config(),
            "20260724_0001",
        )
        run_id = uuid.uuid4()
        task_id = uuid.uuid4()
        source_id = uuid.uuid4()
        claim_id = uuid.uuid4()

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO research_runs (
                        id, question, status
                    ) VALUES (
                        :id, :question, 'CREATED'
                    )
                    """
                ),
                {
                    "id": run_id,
                    "question": "Legacy migration smoke",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO research_tasks (
                        id,
                        run_id,
                        task_type,
                        question,
                        status,
                        priority,
                        input_data,
                        output_data
                    ) VALUES (
                        :id,
                        :run_id,
                        'web_research',
                        :question,
                        'PENDING',
                        1,
                        '{}'::jsonb,
                        '{}'::jsonb
                    )
                    """
                ),
                {
                    "id": task_id,
                    "run_id": run_id,
                    "question": (
                        "Can legacy provenance migrate?"
                    ),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO sources (
                        id,
                        run_id,
                        url,
                        canonical_url,
                        title,
                        content_hash,
                        mime_type,
                        local_path,
                        metadata_json
                    ) VALUES (
                        :id,
                        :run_id,
                        :url,
                        :url,
                        'Legacy source',
                        :content_hash,
                        'text/plain',
                        '/tmp/legacy-source.txt',
                        '{}'::jsonb
                    )
                    """
                ),
                {
                    "id": source_id,
                    "run_id": run_id,
                    "url": (
                        "https://example.com/legacy"
                    ),
                    "content_hash": "a" * 64,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO claims (
                        id,
                        run_id,
                        source_id,
                        text,
                        evidence_quote,
                        locator,
                        status,
                        created_by_agent
                    ) VALUES (
                        :id,
                        :run_id,
                        :source_id,
                        'Legacy claim text',
                        'Legacy evidence quote',
                        CAST(:locator AS jsonb),
                        'UNVERIFIED',
                        'researcher-v1'
                    )
                    """
                ),
                {
                    "id": claim_id,
                    "run_id": run_id,
                    "source_id": source_id,
                    "locator": (
                        '{"task_id": "'
                        + str(task_id)
                        + '"}'
                    ),
                },
            )

        command.upgrade(
            alembic_config(),
            "head",
        )

        with engine.connect() as connection:
            migrated = connection.execute(
                text(
                    """
                    SELECT
                        claim.research_task_id,
                        claim.source_snapshot_id,
                        snapshot.source_id,
                        snapshot.content_hash
                    FROM claims AS claim
                    JOIN source_snapshots AS snapshot
                      ON snapshot.id =
                         claim.source_snapshot_id
                    WHERE claim.id = :claim_id
                    """
                ),
                {
                    "claim_id": claim_id,
                },
            ).one()

        assert migrated.research_task_id == task_id
        assert migrated.source_snapshot_id is not None
        assert migrated.source_id == source_id
        assert migrated.content_hash == "a" * 64
        engine.dispose()
    finally:
        with psycopg.connect(
            admin_conninfo,
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL(
                    "DROP DATABASE IF EXISTS {} "
                    "WITH (FORCE)"
                ).format(
                    sql.Identifier(database_name)
                )
            )

    print("Alembic clean upgrade/downgrade smoke test OK")


if __name__ == "__main__":
    main()
