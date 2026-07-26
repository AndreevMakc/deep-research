from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def sqlalchemy_database_url() -> str:
    settings = get_settings()
    url = settings.database_url

    if url.startswith("postgresql://"):
        return url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    return url


engine: Engine = create_engine(
    sqlalchemy_database_url(),
    pool_pre_ping=True,
    echo=False,
)

SessionFactory = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db_session() -> Generator[Session, None, None]:
    session = SessionFactory()

    try:
        yield session
    finally:
        session.close()