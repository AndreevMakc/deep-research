import uuid

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from app.models import create_planner_model
from app.budget import consume_run_budget, estimate_tokens
from app.prompts import load_prompt
from app.resilience import retry_external_call
from app.schemas.research_plan import ResearchPlan

import logging

logger = logging.getLogger(__name__)


def generate_research_plan(
    question: str,
    *,
    run_id: uuid.UUID | None = None,
) -> ResearchPlan:
    if not question.strip():
        raise ValueError(
            "Research question must not be empty"
        )

    logger.info(
        "Generating research plan for question length=%s",
        len(question),
    )

    model = create_planner_model()

    structured_model = model.with_structured_output(
        ResearchPlan,
        method="json_schema",
        strict=True,
    )

    system_prompt = load_prompt("planner-v1.md")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                "Создай план исследования для следующего "
                f"вопроса:\n\n{question.strip()}"
            )
        ),
    ]

    if run_id is not None:
        consume_run_budget(
            run_id,
            external_requests=1,
            tokens=estimate_tokens(
                system_prompt + question
            ),
        )

    result = retry_external_call(
        "planner_llm",
        structured_model.invoke,
        messages,
    )

    if not isinstance(result, ResearchPlan):
        raise TypeError(
            "Planner returned an unexpected result type"
        )

    logger.info(
        "Planner generated %s subtasks",
        len(result.subquestions),
    )

    return result
