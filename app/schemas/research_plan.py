from pydantic import BaseModel, Field, field_validator


class ResearchSubtask(BaseModel):
    """One independent task in a research plan."""

    title: str = Field(
        min_length=3,
        max_length=200,
        description="Краткое название исследовательской подзадачи.",
    )

    question: str = Field(
        min_length=10,
        max_length=1000,
        description=(
            "Конкретный вопрос, на который должен ответить "
            "Researcher-агент."
        ),
    )

    objective: str = Field(
        min_length=10,
        max_length=1000,
        description=(
            "Какой результат должна дать подзадача и зачем он нужен."
        ),
    )

    source_types: list[str] = Field(
        min_length=1,
        max_length=5,
        description=(
            "Предпочтительные типы источников: официальная "
            "документация, первичные исследования, нормативные "
            "документы и так далее."
        ),
    )

    search_queries: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Стартовые поисковые запросы.",
    )

    priority: int = Field(
        ge=1,
        le=100,
        description=(
            "Приоритет задачи. 1 — самый высокий, "
            "100 — самый низкий."
        ),
    )

    @field_validator("source_types", "search_queries")
    @classmethod
    def remove_empty_values(
        cls,
        values: list[str],
    ) -> list[str]:
        cleaned = [
            value.strip()
            for value in values
            if value.strip()
        ]

        if not cleaned:
            raise ValueError(
                "List must contain at least one non-empty value"
            )

        return cleaned


class ResearchPlan(BaseModel):
    """Structured plan created by the Planner agent."""

    normalized_question: str = Field(
        min_length=10,
        max_length=2000,
        description=(
            "Уточнённая формулировка исходного вопроса без "
            "изменения намерения пользователя."
        ),
    )

    scope: str = Field(
        min_length=10,
        max_length=2000,
        description=(
            "Что входит в исследование и какие ограничения "
            "нужно соблюдать."
        ),
    )

    subquestions: list[ResearchSubtask] = Field(
        min_length=2,
        max_length=6,
        description=(
            "Независимые исследовательские подзадачи без "
            "существенного дублирования."
        ),
    )

    completion_criteria: list[str] = Field(
        min_length=2,
        max_length=8,
        description=(
            "Проверяемые условия, при которых исследование "
            "можно считать достаточно полным."
        ),
    )

    assumptions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Предположения Planner-а, которые в дальнейшем "
            "нужно проверить или явно указать в отчёте."
        ),
    )