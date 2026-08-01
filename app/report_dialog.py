from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.budget import consume_run_budget, estimate_tokens
from app.config import get_settings
from app.db.models import ResearchTask, TaskStatus
from app.db.repositories import (
    get_research_report,
    snapshot_research_report_version,
)
from app.db.session import SessionFactory
from app.models import create_writer_model
from app.resilience import (
    ExternalOutputValidationError,
    retry_external_call,
)


SYSTEM_PROMPT = """Ты отвечаешь только по сохранённому отчёту.
Не используй память, общие знания и предположения. Если корпус не даёт
достаточного ответа, поставь needs_research=true и кратко опиши, каких
данных не хватает. Иначе дай прямой ответ и укажи только существующие
claim_ids и section_ids из корпуса. Не создавай ссылки и источники."""


class ReportAnswerDraft(BaseModel):
    answer: str | None = Field(default=None, max_length=8_000)
    claim_ids: list[str] = Field(default_factory=list, max_length=20)
    section_ids: list[str] = Field(default_factory=list, max_length=12)
    needs_research: bool = False
    missing_information: str | None = Field(
        default=None,
        max_length=2_000,
    )


def report_sections(result: dict) -> list[dict]:
    sections: list[dict] = []

    def add(identifier: str, title: str, statements: list[dict]) -> None:
        cleaned = [
            {
                "text": statement.get("text", ""),
                "claim_ids": statement.get("claim_ids", []),
            }
            for statement in statements
            if statement and statement.get("text")
        ]
        if cleaned:
            sections.append({"id": identifier, "title": title, "statements": cleaned})

    direct = result.get("direct_answer")
    if direct is None:
        short = result.get("short_answer") or []
        direct = short[0] if short else None
    add("report-answer", "Прямой ответ", [direct] if direct else [])
    add(
        "report-findings",
        "Ключевые выводы",
        [finding.get("statement", {}) for finding in result.get("key_findings", [])],
    )
    for index, section in enumerate(result.get("sections", [])):
        add(
            f"report-section-{index}",
            section.get("heading") or f"Раздел {index + 1}",
            section.get("statements", []),
        )
    add(
        "report-caveats",
        "Противоречия и ограничения",
        result.get("contradictions", []),
    )
    return sections


def validate_report_answer(
    draft: ReportAnswerDraft,
    result: dict,
) -> dict:
    sections = report_sections(result)
    sections_by_id = {section["id"]: section for section in sections}
    sources_by_claim = {
        source.get("claim_id"): source
        for source in result.get("sources", [])
        if source.get("claim_id")
    }
    unknown_sections = set(draft.section_ids) - set(sections_by_id)
    unknown_claims = set(draft.claim_ids) - set(sources_by_claim)

    if unknown_sections or unknown_claims:
        raise ValueError("Answer references unknown report evidence")

    section_claims = {
        claim_id
        for section_id in draft.section_ids
        for statement in sections_by_id[section_id]["statements"]
        for claim_id in statement["claim_ids"]
    }
    if not set(draft.claim_ids).issubset(section_claims):
        raise ValueError("Answer claim is not present in cited sections")

    if (draft.answer or "").strip() and (
        not draft.claim_ids or not draft.section_ids
    ):
        raise ValueError("Every answer must cite report evidence")

    if draft.needs_research:
        if not (draft.missing_information or "").strip():
            raise ValueError("Missing information must be explained")
    elif (
        not (draft.answer or "").strip() or not draft.claim_ids or not draft.section_ids
    ):
        raise ValueError("Grounded answer requires report citations")

    return {
        "answer": draft.answer,
        "needs_research": draft.needs_research,
        "missing_information": draft.missing_information,
        "sections": [
            {
                "id": section_id,
                "title": sections_by_id[section_id]["title"],
            }
            for section_id in dict.fromkeys(draft.section_ids)
        ],
        "citations": [
            sources_by_claim[claim_id] for claim_id in dict.fromkeys(draft.claim_ids)
        ],
    }


def answer_report_question(
    *,
    run_id: uuid.UUID,
    question: str,
    result: dict,
    generate: Callable[[list], ReportAnswerDraft] | None = None,
) -> dict:
    question = question.strip()
    if len(question) < 3:
        raise ValueError("Question must contain at least 3 characters")

    corpus = {
        "sections": report_sections(result),
        "sources": [
            {
                key: source.get(key)
                for key in (
                    "claim_id",
                    "source_title",
                    "evidence_quote",
                    "verdict",
                    "verification_reason",
                )
            }
            for source in result.get("sources", [])
        ],
    }
    content = json.dumps(corpus, ensure_ascii=False)
    consume_run_budget(
        run_id,
        external_requests=1,
        tokens=estimate_tokens(SYSTEM_PROMPT + question + content),
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(f"Вопрос: {question}\n\nСохранённый корпус:\n{content}")),
    ]

    if generate is None:
        model = create_writer_model()
        method = (
            "function_calling"
            if get_settings().llm_provider.strip().lower() == "google"
            else "json_schema"
        )
        structured = model.with_structured_output(
            ReportAnswerDraft,
            method=method,
            strict=True,
        )
        generate = structured.invoke

    def invoke_and_validate() -> ReportAnswerDraft:
        draft = generate(messages)
        if not isinstance(draft, ReportAnswerDraft):
            raise TypeError("Report dialog returned an unexpected type")
        try:
            validate_report_answer(draft, result)
        except ValueError as error:
            raise ExternalOutputValidationError(str(error)) from error
        return draft

    draft = retry_external_call("report_dialog_llm", invoke_and_validate)
    return validate_report_answer(draft, result)


def report_change_summary(previous: dict, current: dict) -> str:
    def source_ids(value: dict) -> set[str]:
        return {
            source.get("source_snapshot_id") or source.get("claim_id")
            for source in value.get("sources", [])
            if source.get("source_snapshot_id") or source.get("claim_id")
        }

    before = source_ids(previous)
    after = source_ids(current)
    changes: list[str] = []
    if after - before:
        changes.append(f"добавлено источников: {len(after - before)}")
    if before - after:
        changes.append(f"убрано источников: {len(before - after)}")
    if previous.get("direct_answer") != current.get("direct_answer"):
        changes.append("обновлён прямой ответ")
    return (
        "; ".join(changes).capitalize() + "."
        if changes
        else "Подтверждённый вывод не изменился."
    )


def finalize_follow_up_version(task_id: uuid.UUID) -> int:
    with SessionFactory() as session:
        task = session.get(ResearchTask, task_id)
        if task is None:
            raise RuntimeError(f"Research task not found: {task_id}")
        existing = task.output_data.get("report_version_number")
        if existing:
            return int(existing)
        if task.status != TaskStatus.COMPLETED:
            raise RuntimeError("Follow-up research task is not completed")
        report = get_research_report(session, task.run_id)
        if report is None:
            raise RuntimeError("Follow-up research produced no report")
        version = snapshot_research_report_version(
            session,
            report=report,
            reason=task.input_data["follow_up_reason"],
            requested_by=task.input_data.get("requested_by"),
            force=True,
        )
        task.output_data = {
            **task.output_data,
            "report_version_number": version.version_number,
        }
        session.commit()
        return version.version_number
