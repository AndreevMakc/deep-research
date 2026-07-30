import uuid
from collections.abc import Callable
from functools import wraps
from threading import BoundedSemaphore

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agents.planner import generate_research_plan
from app.agents.researcher import research_task_node
from app.agents.verifier import (
    VERIFIER_AGENT,
    verifier_task_node,
)
from app.agents.writer import writer_node
from app.config import get_settings
from app.db.models import TaskStatus
from app.db.repositories import (
    create_research_tasks,
    get_claim_ids_needing_verification,
    get_tasks_for_run,
    get_verifications_for_claims,
)
from app.db.session import SessionFactory
from app.observability import observed_node
from app.state import ResearchState


def _bounded_node(
    node: Callable[[dict], dict],
    limit: int,
) -> Callable[[dict], dict]:
    semaphore = BoundedSemaphore(max(1, limit))

    @wraps(node)
    def wrapped(state: dict) -> dict:
        with semaphore:
            return node(state)

    return wrapped


def create_plan(state: ResearchState) -> dict:
    """Create a plan or resume only unfinished persisted tasks."""

    run_id = uuid.UUID(state["run_id"])

    with SessionFactory() as session:
        existing_tasks = get_tasks_for_run(
            session=session,
            run_id=run_id,
        )

        if not existing_tasks:
            plan = generate_research_plan(
                question=state["question"],
                run_id=run_id,
                research_input=state.get(
                    "research_input",
                    {},
                ),
            )
            plan_data = plan.model_dump(mode="json")
            tasks = create_research_tasks(
                session=session,
                run_id=run_id,
                tasks=[
                    subtask.model_dump(mode="json")
                    for subtask in plan.subquestions
                ],
            )
        else:
            tasks = existing_tasks
            plan_data = state.get("plan") or {
                "normalized_question": state["question"],
                "scope": (
                    "Existing persisted research tasks "
                    "are being resumed."
                ),
                "subquestions": [
                    {
                        "title": task.input_data.get(
                            "title",
                            task.question,
                        ),
                        "question": task.question,
                        **task.input_data,
                        "priority": task.priority,
                    }
                    for task in tasks
                ],
                "completion_criteria": [],
                "assumptions": [],
            }

        runnable_tasks = (
            []
            if state.get("finish_early", False)
            else [
                task
                for task in tasks
                if task.status != TaskStatus.COMPLETED
            ]
        )
        completed_tasks = [
            task
            for task in tasks
            if task.status == TaskStatus.COMPLETED
        ]

    return {
        "plan": plan_data,
        "task_ids": [
            str(task.id)
            for task in runnable_tasks
        ],
        "findings": [
            {
                "task_id": str(task.id),
                "result": dict(task.output_data),
            }
            for task in completed_tasks
        ],
        "claim_ids": [
            claim_id
            for task in completed_tasks
            for claim_id in task.output_data.get(
                "claim_ids",
                [],
            )
        ],
    }


def assign_researchers(
    state: ResearchState,
) -> list[Send] | str:
    """Fan out one worker per unfinished research task."""

    sends = [
        Send(
            "researcher",
            {
                "run_id": state["run_id"],
                "task_id": task_id,
                "findings": [],
            },
        )
        for task_id in state["task_ids"]
    ]

    return sends or "research_join"


def collect_pending_claims(
    state: ResearchState,
) -> dict:
    """Select claims that do not have a persisted verdict."""

    run_id = uuid.UUID(state["run_id"])
    claim_ids = list(
        dict.fromkeys(state.get("claim_ids", []))
    )

    with SessionFactory() as session:
        parsed_claim_ids = [
            uuid.UUID(claim_id)
            for claim_id in claim_ids
        ]
        pending = get_claim_ids_needing_verification(
            session=session,
            run_id=run_id,
            claim_ids=parsed_claim_ids,
            verifier_agent=VERIFIER_AGENT,
        )
        existing = get_verifications_for_claims(
            session=session,
            claim_ids=parsed_claim_ids,
            verifier_agent=VERIFIER_AGENT,
        )

    return {
        "pending_claim_ids": [
            str(claim_id)
            for claim_id in pending
        ],
        "verifications": [
            {
                "claim_id": str(verification.claim_id),
                "verdict": verification.verdict.value,
                "confidence": verification.confidence,
                "reason": verification.reason,
            }
            for verification in existing
        ],
    }


def assign_verifiers(
    state: ResearchState,
) -> list[Send] | str:
    """Fan out one independent worker per unverified claim."""

    sends = [
        Send(
            "verifier",
            {
                "run_id": state["run_id"],
                "claim_id": claim_id,
                "verifications": [],
            },
        )
        for claim_id in state.get(
            "pending_claim_ids",
            [],
        )
    ]

    return sends or "writer"


def create_report(state: ResearchState) -> dict:
    task_order = {
        task_id: index
        for index, task_id in enumerate(
            state["task_ids"]
        )
    }
    ordered_findings = sorted(
        state["findings"],
        key=lambda finding: task_order.get(
            finding["task_id"],
            len(task_order),
        ),
    )
    sections: list[str] = []

    for index, finding in enumerate(
        ordered_findings,
        start=1,
    ):
        result = finding["result"]
        error = result.get("error")

        if error:
            sections.append(
                f"{index}. Researcher task failed\n"
                f"{error.get('message', 'Unknown error')}"
            )
        else:
            sections.append(
                f"{index}. {result['task_question']}\n"
                f"{result['summary']}"
            )

    body = "\n\n".join(sections)
    verifications = state.get("verifications", [])
    completed_verifications = [
        verification
        for verification in verifications
        if "verdict" in verification
    ]
    failed_verifications = [
        verification
        for verification in verifications
        if "error" in verification
    ]
    verification_summary = (
        "\n\nПроверка claims: "
        f"{len(completed_verifications)} завершено, "
        f"{len(failed_verifications)} с ошибкой."
    )

    return {
        "report": (
            f"Вопрос: {state['question']}\n\n"
            f"Промежуточные результаты Researcher:\n{body}"
            f"{verification_summary}"
        )
    }


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
):
    settings = get_settings()
    builder = StateGraph(ResearchState)

    builder.add_node(
        "planner",
        observed_node(create_plan, agent="planner"),
    )
    builder.add_node(
        "researcher",
        _bounded_node(
            observed_node(
                research_task_node,
                agent="researcher",
            ),
            settings.max_parallel_researchers,
        ),
    )
    builder.add_node(
        "research_join",
        observed_node(
            collect_pending_claims,
            agent="research_join",
        ),
    )
    builder.add_node(
        "verifier",
        _bounded_node(
            observed_node(
                verifier_task_node,
                agent="verifier",
            ),
            settings.max_parallel_verifiers,
        ),
    )
    builder.add_node(
        "writer",
        observed_node(writer_node, agent="writer"),
    )

    builder.add_edge(START, "planner")
    builder.add_conditional_edges(
        "planner",
        assign_researchers,
        ["researcher", "research_join"],
    )
    builder.add_edge("researcher", "research_join")
    builder.add_conditional_edges(
        "research_join",
        assign_verifiers,
        ["verifier", "writer"],
    )
    builder.add_edge("verifier", "writer")
    builder.add_edge("writer", END)

    return builder.compile(checkpointer=checkpointer)
