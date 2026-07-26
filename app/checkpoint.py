from contextlib import contextmanager
from collections.abc import Iterator

from langgraph.checkpoint.postgres import PostgresSaver

from app.config import get_settings


@contextmanager
def postgres_checkpointer() -> Iterator[PostgresSaver]:
    settings = get_settings()

    with PostgresSaver.from_conn_string(
        settings.database_url
    ) as checkpointer:
        yield checkpointer


def initialize_checkpointer() -> None:
    with postgres_checkpointer() as checkpointer:
        checkpointer.setup()


if __name__ == "__main__":
    initialize_checkpointer()
    print("LangGraph checkpoint tables created")