from pydantic import BaseModel, Field, field_validator


class SearchSource(BaseModel):
    """One source found during web research."""

    title: str = Field(
        min_length=1,
        max_length=1000,
        description="Название страницы или документа.",
    )

    url: str = Field(
        min_length=8,
        max_length=4000,
        description="Абсолютный URL источника.",
    )

    content: str = Field(
        min_length=1,
        max_length=12000,
        description=(
            "Фрагмент или краткое содержание, полученное "
            "непосредственно из поискового инструмента."
        ),
    )

    relevance_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Оценка релевантности поисковой системой.",
    )

    query: str = Field(
        min_length=1,
        max_length=2000,
        description="Поисковый запрос, по которому найден источник.",
    )


class ResearchFinding(BaseModel):
    """One finding produced by the Researcher agent."""

    statement: str = Field(
        min_length=10,
        max_length=3000,
        description=(
            "Утверждение, сформулированное только на основании "
            "доступных результатов поиска."
        ),
    )

    source_url: str = Field(
        min_length=8,
        max_length=4000,
        description=(
            "URL одного источника, непосредственно "
            "поддерживающего утверждение."
        ),
    )

    evidence_quote: str = Field(
        min_length=5,
        max_length=5000,
        description=(
            "Дословная цитата из полного сохранённого текста "
            "источника."
        ),
    )

    locator: str | None = Field(
        default=None,
        max_length=1000,
        description=(
            "Раздел, страница или другое местоположение цитаты."
        ),
    )

    scope: str | None = Field(
        default=None,
        max_length=3000,
        description=(
            "Область применимости утверждения."
        ),
    )

    limitations: str | None = Field(
        default=None,
        max_length=3000,
        description=(
            "Ограничения утверждения, неполнота данных "
            "или противоречия."
        ),
    )


class ResearchTaskResult(BaseModel):
    """Structured output of one Researcher task."""

    task_question: str = Field(
        min_length=10,
        max_length=2000,
    )

    summary: str = Field(
        min_length=20,
        max_length=5000,
        description=(
            "Краткий ответ на исследовательскую подзадачу."
        ),
    )

    findings: list[ResearchFinding] = Field(
        default_factory=list,
        max_length=10,
    )

    source_urls: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Все источники, реально использованные "
            "для формирования результата."
        ),
    )

    unanswered_questions: list[str] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("source_urls")
    @classmethod
    def deduplicate_source_urls(
        cls,
        values: list[str],
    ) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        for value in values:
            normalized = value.strip()

            if normalized and normalized not in seen:
                result.append(normalized)
                seen.add(normalized)

        return result
