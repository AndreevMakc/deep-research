from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db.session import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "20260724_0001"

LEGACY_TABLES = {
    "research_runs",
    "research_tasks",
    "sources",
    "claims",
    "verifications",
}


def alembic_config() -> Config:
    return Config(str(ALEMBIC_CONFIG_PATH))


def bootstrap_existing_database() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "alembic_version" in table_names:
        return

    existing_legacy_tables = (
        table_names & LEGACY_TABLES
    )

    if not existing_legacy_tables:
        return

    if existing_legacy_tables != LEGACY_TABLES:
        missing = sorted(
            LEGACY_TABLES - existing_legacy_tables
        )
        raise RuntimeError(
            "Cannot bootstrap partially initialized "
            f"database; missing tables: {missing}"
        )

    command.stamp(
        alembic_config(),
        BASELINE_REVISION,
    )


def upgrade_database(revision: str = "head") -> None:
    bootstrap_existing_database()
    command.upgrade(
        alembic_config(),
        revision,
    )


if __name__ == "__main__":
    upgrade_database()
    print("Application database migrations applied")
