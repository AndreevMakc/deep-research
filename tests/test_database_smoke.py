from app.db.repositories import (
    create_research_run,
    get_research_run,
)
from app.db.session import SessionFactory


def main() -> None:
    with SessionFactory() as session:
        created = create_research_run(
            session=session,
            question="Как проверить работу общей памяти агентов?",
        )

        loaded = get_research_run(
            session=session,
            run_id=created.id,
        )

        assert loaded is not None
        assert loaded.id == created.id
        assert loaded.question == created.question

        print(f"Created run: {created.id}")
        print(f"Loaded question: {loaded.question}")
        print("Database smoke test OK")


if __name__ == "__main__":
    main()