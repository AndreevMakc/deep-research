from unittest.mock import patch

from app.agents.researcher import research_task_node
from app.db.models import TaskStatus
from app.db.repositories import (
    create_research_run,
    create_research_tasks,
    get_research_task,
)
from app.db.session import SessionFactory


def main() -> None:
    with SessionFactory() as session:
        run = create_research_run(
            session=session,
            question="Can a worker fail independently?",
        )
        task = create_research_tasks(
            session=session,
            run_id=run.id,
            tasks=[
                {
                    "title": "Expected failure",
                    "question": (
                        "Does one failed worker stop the run?"
                    ),
                    "objective": (
                        "Confirm partial failure persistence."
                    ),
                    "source_types": ["test"],
                    "search_queries": ["failure"],
                    "priority": 1,
                }
            ],
        )[0]
        run_id = run.id
        task_id = task.id

    with (
        patch(
            "app.agents.researcher."
            "collect_search_sources",
            side_effect=RuntimeError(
                "simulated worker failure"
            ),
        ),
        patch(
            "app.agents.researcher.logger.exception"
        ),
    ):
        result = research_task_node(
            {
                "run_id": str(run_id),
                "task_id": str(task_id),
                "findings": [],
            }
        )

    assert result["claim_ids"] == []
    assert (
        result["findings"][0]["result"]["error"]["code"]
        == "unexpected_researcher_error"
    )

    with SessionFactory() as session:
        persisted_task = get_research_task(
            session=session,
            task_id=task_id,
            run_id=run_id,
        )

        assert persisted_task is not None
        assert persisted_task.status == TaskStatus.FAILED
        assert (
            persisted_task.output_data["error"]["message"]
            == "simulated worker failure"
        )

        session.delete(persisted_task.run)
        session.commit()

    print("Partial worker failure smoke test OK")


if __name__ == "__main__":
    main()
