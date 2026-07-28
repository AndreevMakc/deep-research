from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DraftInterpretation:
    scope: str
    period: str
    assumptions: list[str]
    estimated_duration_minutes: int


def interpret_research_question(
    question: str,
    *,
    max_run_seconds: int,
) -> DraftInterpretation:
    normalized = " ".join(question.split())
    estimated_minutes = max(
        5,
        min(60, math.ceil(max_run_seconds / 60)),
    )
    return DraftInterpretation(
        scope=(
            "Подготовить проверяемый ответ на вопрос: "
            f"«{normalized}»"
        ),
        period=(
            "Актуальные доступные данные на момент запуска"
        ),
        assumptions=[
            (
                "Ключевые выводы проверяются по "
                "независимым источникам"
            ),
            (
                "Противоречия и нехватка доказательств "
                "указываются явно"
            ),
        ],
        estimated_duration_minutes=estimated_minutes,
    )
