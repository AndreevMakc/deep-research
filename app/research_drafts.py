from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DraftInterpretation:
    scope: str
    period: str
    assumptions: list[str]
    estimated_duration_minutes: int


@dataclass(frozen=True)
class ClarificationQuestion:
    id: str
    prompt: str
    options: list[str]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "options": list(self.options),
        }


def build_clarification_questions(
    question: str,
) -> list[ClarificationQuestion]:
    normalized = " ".join(question.split())
    lowered = normalized.casefold()
    has_period = bool(
        re.search(
            r"\b(20\d{2}|год|года|лет|месяц|квартал|"
            r"недел|сейчас|текущ|последн|недавн)\w*",
            lowered,
        )
    )
    has_audience = any(
        marker in lowered
        for marker in (
            " для ",
            " чтобы ",
            " с целью ",
            " пользовател",
            " команд",
            " компани",
        )
    )
    has_criteria = any(
        marker in lowered
        for marker in (
            " по ",
            "с учётом",
            "с учетом",
            "критери",
            "стоимост",
            "безопасност",
            "скорост",
            "качеств",
        )
    )
    has_geography = any(
        marker in lowered
        for marker in (
            "росси",
            "европ",
            "сша",
            "азии",
            "глобаль",
            "международ",
            "регион",
            "стране",
        )
    )
    is_specific = (
        len(normalized) >= 80
        and sum(
            (
                has_period,
                has_audience,
                has_criteria,
                has_geography,
            )
        )
        >= 3
    )

    if is_specific:
        return []

    questions: list[ClarificationQuestion] = []

    if len(normalized) < 100 or not has_criteria:
        questions.append(
            ClarificationQuestion(
                id="scope",
                prompt=(
                    "Какой охват будет наиболее полезен "
                    "для этого исследования?"
                ),
                options=[
                    "Один конкретный объект или решение",
                    "Сравнение нескольких вариантов",
                    "Обзор всей области",
                ],
            )
        )

    if not has_audience:
        questions.append(
            ClarificationQuestion(
                id="purpose",
                prompt=(
                    "Для какого решения или аудитории "
                    "нужен результат?"
                ),
                options=[
                    "Для выбора продукта или поставщика",
                    "Для внутренней стратегии",
                    "Для общего понимания темы",
                ],
            )
        )

    if not has_period:
        questions.append(
            ClarificationQuestion(
                id="period",
                prompt="Какой временной период учитывать?",
                options=[
                    "Текущее состояние",
                    "Последние 12 месяцев",
                    "Последние 3 года",
                ],
            )
        )

    market_sensitive = any(
        marker in lowered
        for marker in (
            "рын",
            "цен",
            "закон",
            "регулир",
            "поставщик",
        )
    )

    if market_sensitive and not has_geography:
        questions.append(
            ClarificationQuestion(
                id="geography",
                prompt="Какую географию учитывать?",
                options=[
                    "Россия",
                    "Европа и США",
                    "Глобальный рынок",
                ],
            )
        )

    if len(questions) < 2:
        questions.append(
            ClarificationQuestion(
                id="priority",
                prompt=(
                    "Какой критерий результата наиболее важен?"
                ),
                options=[
                    "Практические рекомендации",
                    "Сравнение доказательств",
                    "Риски и ограничения",
                ],
            )
        )

    return questions[:4]


def refine_interpretation_with_answers(
    interpretation: DraftInterpretation,
    *,
    questions: list[dict],
    answers: list[dict],
) -> DraftInterpretation:
    question_by_id = {
        question["id"]: question
        for question in questions
    }
    effective_answers = [
        answer
        for answer in answers
        if not answer.get("skipped") and answer.get("answer")
    ]
    period = interpretation.period
    scope_context: list[str] = []
    assumptions = list(interpretation.assumptions)

    for answer in effective_answers:
        question = question_by_id.get(
            answer["question_id"],
            {},
        )
        prompt = question.get(
            "prompt",
            "Уточнение пользователя",
        )
        value = answer["answer"]

        if answer["question_id"] == "period":
            period = value
        else:
            scope_context.append(value)

        assumptions.append(f"{prompt}: {value}")

    scope = interpretation.scope

    if scope_context:
        scope += " Уточнённый контекст: " + "; ".join(
            scope_context
        )

    return DraftInterpretation(
        scope=scope,
        period=period,
        assumptions=assumptions,
        estimated_duration_minutes=(
            interpretation.estimated_duration_minutes
        ),
    )


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
