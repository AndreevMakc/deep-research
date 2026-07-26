from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.session import sqlalchemy_database_url


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = sqlalchemy_database_url().replace(
    "%",
    "%%",
)
config.set_main_option(
    "sqlalchemy.url",
    database_url,
)

target_metadata = Base.metadata

EXTERNALLY_MANAGED_TABLES = {
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
}


def include_object(
    object_,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to,
) -> bool:
    if (
        type_ == "table"
        and name in EXTERNALLY_MANAGED_TABLES
    ):
        return False

    table = getattr(object_, "table", None)

    if (
        table is not None
        and table.name in EXTERNALLY_MANAGED_TABLES
    ):
        return False

    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
