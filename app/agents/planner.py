import uuid
import logging

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from app.budget import consume_run_budget, estimate_tokens
from app.models import create_planner_model
from app.prompts import load_prompt
from app.resilience import retry_external_call
from app.research_inputs import (
    render_research_input_context,
)
from app.schemas.research_plan import ResearchPlan

logger = logging.getLogger(__name__)


def generate_research_plan(
    question: str,
    *,
    run_id: uuid.UUID | None = None,
    research_input: dict | None = None,
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
    input_context = render_research_input_context(
        research_input or {}
    )
    user_content = (
        "Создай план исследования для следующего "
        f"вопроса:\n\n{question.strip()}"
    )

    if input_context:
        user_content += (
            "\n\nПодтверждённые пользовательские материалы "
            "и настройки. Соблюдай роли материалов: "
            "`verify` требует независимой проверки, "
            "`primary_source` является первичным источником, "
            "`context_only` задаёт контекст, "
            "`do_not_cite` нельзя цитировать.\n\n"
            f"{input_context}"
        )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    if run_id is not None:
        consume_run_budget(
            run_id,
            external_requests=1,
            tokens=estimate_tokens(
                system_prompt + question
                + input_context
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
