import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.checkpoint import postgres_checkpointer
from app.config import get_settings
from app.db.models import (
    ClaimStatus,
    EventStatus,
    ResearchRun,
    RunStatus,
    TaskStatus,
    WorkItem,
)
from app.db.repositories import (
    create_research_run,
    get_claims_for_run,
    get_research_run,
    get_tasks_for_run,
)
from app.db.session import SessionFactory
from app.error_handling import classify_expected_error
from app.graph import build_graph
from app.observability import (
    configure_structured_logging,
    emit_event,
    observability_context,
)
from app.report_dialog import finalize_follow_up_version


def update_run_status(
    run_id,
    status: RunStatus,
) -> None:
    with SessionFactory() as session:
        run = session.get(ResearchRun, run_id)

        if run is None:
            raise RuntimeError(
                f"Research run not found: {run_id}"
            )

        if run.status in {
            RunStatus.PAUSE_REQUESTED,
            RunStatus.PAUSED,
            RunStatus.CANCEL_REQUESTED,
            RunStatus.CANCELLED,
        }:
            return

        run.status = status

        if (
            status == RunStatus.RUNNING
            and run.started_at is None
        ):
            run.started_at = datetime.now(timezone.utc)

        session.commit()


def resolve_run_status(
    task_statuses: list[TaskStatus],
    claim_statuses: list[ClaimStatus],
) -> RunStatus:
    completed_tasks = sum(
        status == TaskStatus.COMPLETED
        for status in task_statuses
    )

    if not task_statuses or completed_tasks == 0:
        return RunStatus.FAILED

    if (
        completed_tasks != len(task_statuses)
        or ClaimStatus.UNVERIFIED in claim_statuses
    ):
        return RunStatus.COMPLETED_WITH_ERRORS

    return RunStatus.COMPLETED


def persisted_run_status(run_id: uuid.UUID) -> RunStatus:
    with SessionFactory() as session:
        tasks = get_tasks_for_run(
            session=session,
            run_id=run_id,
        )
        claims = get_claims_for_run(
            session=session,
            run_id=run_id,
        )

    return resolve_run_status(
        [task.status for task in tasks],
        [claim.status for claim in claims],
    )


def main() -> int:
    configure_structured_logging(
        get_settings().log_level
    )
    arguments = sys.argv[1:]
    resume_run_id: uuid.UUID | None = None
    finish_early = False

    if arguments[:1] == ["--resume"]:
        if (
            len(arguments) not in {2, 3}
            or (
                len(arguments) == 3
                and arguments[2] != "--finish-early"
            )
        ):
            print(
                "Usage: python -m app.main --resume <run-id> "
                "[--finish-early]",
                file=sys.stderr,
            )
            return 2

        try:
            resume_run_id = uuid.UUID(arguments[1])
        except ValueError:
            print(
                "Run ID must be a valid UUID.",
                file=sys.stderr,
            )
            return 2

        finish_early = len(arguments) == 3
        question = ""
    else:
        question = " ".join(arguments).strip()

    if not question and resume_run_id is None:
        question = "Как многоагентные системы улучшают deep research?"

    run_id = None
    research_input: dict = {}
    follow_up_task_id: uuid.UUID | None = None
    final_status: RunStatus | None = None

    try:
        if resume_run_id is None:
            settings = get_settings()
            with SessionFactory() as session:
                research_run = create_research_run(
                    session=session,
                    question=question,
                    limits={
                        "max_external_requests": (
                            settings.max_external_requests
                        ),
                        "max_sources": settings.max_sources,
                        "max_claims": settings.max_claims,
                        "max_tokens": settings.max_tokens,
                        "max_run_seconds": (
                            settings.max_run_seconds
                        ),
                    },
                )
                run_id = research_run.id
        else:
            with SessionFactory() as session:
                research_run = get_research_run(
                    session=session,
                    run_id=resume_run_id,
                )
                work_item = session.scalar(
                    select(WorkItem).where(
                        WorkItem.run_id == resume_run_id,
                        WorkItem.kind
                        == "execute_research_run",
                    )
                )

                if work_item is not None:
                    research_input = dict(
                        work_item.payload.get(
                            "research_input",
                            {},
                        )
                    )
                    raw_follow_up_task_id = (
                        work_item.payload.get(
                            "follow_up_task_id"
                        )
                    )
                    if raw_follow_up_task_id:
                        follow_up_task_id = uuid.UUID(
                            raw_follow_up_task_id
                        )

            if research_run is None:
                print(
                    f"Research run not found: {resume_run_id}",
                    file=sys.stderr,
                )
                return 2

            run_id = research_run.id
            question = research_run.question

        update_run_status(run_id, RunStatus.RUNNING)

        with observability_context(
            run_id=run_id,
            agent="orchestrator",
            correlation_id=str(run_id),
        ):
            emit_event(
                operation="research_run",
                event_type="run_lifecycle",
                status=EventStatus.STARTED,
            )

        initial_state = {
            "run_id": str(run_id),
            "question": question,
            "research_input": research_input,
            "finish_early": finish_early,
            "plan": {},
            "task_ids": [],
            "findings": [],
            "claim_ids": [],
            "pending_claim_ids": [],
            "verifications": [],
            "report": "",
            "report_json": {},
        }

        config = {
            "configurable": {
                "thread_id": (
                    f"{run_id}:finish"
                    if finish_early
                    else str(run_id)
                ),
            },
            "max_concurrency": (
                max(
                    get_settings().max_parallel_researchers,
                    get_settings().max_parallel_verifiers,
                )
            ),
        }

        with postgres_checkpointer() as checkpointer:
            graph = build_graph(checkpointer=checkpointer)

            result = graph.invoke(
                initial_state,
                config=config,
            )

        if follow_up_task_id is not None:
            finalize_follow_up_version(
                follow_up_task_id
            )

        final_status = persisted_run_status(run_id)
        update_run_status(run_id, final_status)

        with observability_context(
            run_id=run_id,
            agent="orchestrator",
            correlation_id=str(run_id),
        ):
            emit_event(
                operation="research_run",
                event_type="run_lifecycle",
                status=(
                    EventStatus.FAILED
                    if final_status == RunStatus.FAILED
                    else EventStatus.SUCCEEDED
                ),
                metadata={
                    "run_status": final_status.value,
                },
            )

    except Exception as error:
        user_error = classify_expected_error(error)

        if run_id is not None:
            try:
                update_run_status(
                    run_id,
                    RunStatus.FAILED,
                )
            except Exception as status_error:
                status_user_error = classify_expected_error(
                    status_error
                )

                if status_user_error is None:
                    raise

                user_error = status_user_error

            with observability_context(
                run_id=run_id,
                agent="orchestrator",
                correlation_id=str(run_id),
            ):
                emit_event(
                    operation="research_run",
                    event_type="run_lifecycle",
                    status=EventStatus.FAILED,
                    error_code=type(error).__name__,
                )

        if user_error is None:
            raise

        print(
            "\n" + user_error.render(
                run_id=(
                    str(run_id)
                    if run_id is not None
                    else None
                ),
            ),
            file=sys.stderr,
        )
        return 2

    print(f"\nRun ID: {run_id}")
    print(
        "Status: "
        + (
            final_status.value
            if final_status is not None
            else RunStatus.FAILED.value
        )
    )
    print("\n=== RESEARCH REPORT ===\n")
    print(result["report"])
    return (
        1
        if final_status == RunStatus.FAILED
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
