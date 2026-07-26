from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from app.budget import consume_run_budget, estimate_tokens
from app.db.models import TaskStatus
from app.db.repositories import (
    create_or_update_claim,
    get_research_task,
    update_research_task,
)
from app.db.session import SessionFactory
from app.error_handling import classify_expected_error
from app.models import create_worker_model
from app.prompts import load_prompt
from app.resilience import (
    ExternalOutputValidationError,
    retry_external_call,
)
from app.schemas.research_result import (
    ResearchTaskResult,
    SearchSource,
)
from app.schemas.source_document import SourceDocument
from app.source_store import persist_source_document
from app.state import ResearchWorkerState
from app.tools.source_fetch import (
    SourceFetchError,
    fetch_source,
)
from app.tools.web_search import search_web


logger = logging.getLogger(__name__)

MAX_SOURCES_PER_TASK = 8
MAX_SOURCE_EXCERPT_CHARS = 12_000


@dataclass(frozen=True)
class PreparedSource:
    """Persisted source and its full downloaded text."""

    source_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    search_source: SearchSource
    document: SourceDocument


def _deduplicate_sources(
    sources: Iterable[SearchSource],
) -> list[SearchSource]:
    by_url: dict[str, SearchSource] = {}

    for source in sources:
        current = by_url.get(source.url)
        current_score = (
            current.relevance_score
            if current and current.relevance_score is not None
            else -1
        )
        new_score = (
            source.relevance_score
            if source.relevance_score is not None
            else -1
        )

        if current is None or new_score > current_score:
            by_url[source.url] = source

    return sorted(
        by_url.values(),
        key=lambda item: (
            item.relevance_score is not None,
            item.relevance_score or 0,
        ),
        reverse=True,
    )[:MAX_SOURCES_PER_TASK]


def collect_search_sources(
    search_queries: list[str],
    *,
    run_id: uuid.UUID | None = None,
    search_fn: Callable[
        [str],
        list[SearchSource],
    ] = search_web,
) -> list[SearchSource]:
    cleaned_queries = [
        query.strip()
        for query in search_queries
        if query.strip()
    ]

    if not cleaned_queries:
        raise ValueError(
            "Research task must contain search queries"
        )

    search_results: list[SearchSource] = []

    for query in cleaned_queries:
        if run_id is not None:
            consume_run_budget(
                run_id,
                external_requests=1,
            )

        search_results.extend(
            retry_external_call(
                "tavily_search",
                search_fn,
                query,
            )
        )

    sources = _deduplicate_sources(search_results)

    if not sources:
        raise RuntimeError(
            "Web search returned no usable sources"
        )

    return sources


def prepare_research_sources(
    run_id: uuid.UUID,
    search_sources: list[SearchSource],
    *,
    fetch_fn: Callable[[str], SourceDocument] = fetch_source,
) -> tuple[list[PreparedSource], list[str]]:
    prepared: list[PreparedSource] = []
    failures: list[str] = []
    seen_snapshot_ids: set[uuid.UUID] = set()

    with SessionFactory() as session:
        for search_source in search_sources:
            try:
                consume_run_budget(
                    run_id,
                    external_requests=1,
                )
                document = retry_external_call(
                    "source_fetch",
                    fetch_fn,
                    search_source.url,
                )
                snapshot = persist_source_document(
                    session=session,
                    run_id=run_id,
                    document=document,
                    search_title=search_source.title,
                    search_query=search_source.query,
                )
            except (
                SourceFetchError,
                OSError,
                ValueError,
            ) as error:
                logger.warning(
                    "Источник недоступен и будет пропущен "
                    "url=%s причина=%s",
                    search_source.url,
                    error,
                )
                failures.append(
                    f"{search_source.url}: {error}"
                )
                continue

            if snapshot.id in seen_snapshot_ids:
                continue

            seen_snapshot_ids.add(snapshot.id)
            prepared.append(
                PreparedSource(
                    source_id=snapshot.source_id,
                    source_snapshot_id=snapshot.id,
                    search_source=search_source,
                    document=document,
                )
            )

    if not prepared:
        raise RuntimeError(
            "No search source could be downloaded "
            "and persisted"
        )

    return prepared, failures


def _relevance_terms(
    task_question: str,
    search_source: SearchSource,
) -> set[str]:
    value = " ".join(
        (
            task_question,
            search_source.query,
            search_source.content,
        )
    ).lower()

    return {
        term
        for term in re.findall(
            r"[\w-]+",
            value,
            flags=re.UNICODE,
        )
        if len(term) >= 4
    }


def _source_excerpt(
    prepared_source: PreparedSource,
    task_question: str,
) -> str:
    content = prepared_source.document.content

    if len(content) <= MAX_SOURCE_EXCERPT_CHARS:
        return content

    terms = _relevance_terms(
        task_question,
        prepared_source.search_source,
    )
    lines = content.splitlines()
    best_line_index = 0
    best_score = -1

    for index, line in enumerate(lines):
        normalized_line = line.lower()
        score = sum(
            1
            for term in terms
            if term in normalized_line
        )

        if score > best_score:
            best_line_index = index
            best_score = score

    best_line = lines[best_line_index]
    center = content.find(best_line)

    if center < 0:
        return content[:MAX_SOURCE_EXCERPT_CHARS]

    before = MAX_SOURCE_EXCERPT_CHARS // 3
    start = max(0, center - before)
    end = min(
        len(content),
        start + MAX_SOURCE_EXCERPT_CHARS,
    )

    if start:
        next_newline = content.find("\n", start)

        if next_newline >= 0:
            start = next_newline + 1

    if end < len(content):
        previous_newline = content.rfind(
            "\n",
            start,
            end,
        )

        if previous_newline > start:
            end = previous_newline

    return content[start:end]


def _validate_evidence(
    result: ResearchTaskResult,
    sources: list[PreparedSource],
) -> None:
    sources_by_url = {
        source.document.url: source
        for source in sources
    }
    available_urls = set(sources_by_url)
    result_urls = set(result.source_urls)
    unknown_result_urls = (
        result_urls - available_urls
    )

    if unknown_result_urls:
        raise ExternalOutputValidationError(
            "Researcher cited URLs absent from source packet: "
            + ", ".join(sorted(unknown_result_urls))
        )

    for finding in result.findings:
        source = sources_by_url.get(
            finding.source_url
        )

        if source is None:
            raise ExternalOutputValidationError(
                "Researcher cited URL absent from source packet: "
                f"{finding.source_url}"
            )

        if finding.source_url not in result_urls:
            raise ExternalOutputValidationError(
                "Finding source URL is absent from "
                "top-level source_urls"
            )

        quote = finding.evidence_quote.strip()

        if quote not in source.document.content:
            raise ExternalOutputValidationError(
                "Evidence quote is absent from stored "
                f"source text: {finding.source_url}"
            )


def generate_research_result(
    task_question: str,
    sources: list[PreparedSource],
    *,
    objective: str = "",
    source_types: list[str] | None = None,
    run_id: uuid.UUID | None = None,
) -> ResearchTaskResult:
    cleaned_question = task_question.strip()

    if not cleaned_question:
        raise ValueError(
            "Research task question must not be empty"
        )

    if not sources:
        raise ValueError(
            "Research task must contain persisted sources"
        )

    source_packet = [
        {
            "source_id": str(source.source_id),
            "source_snapshot_id": str(
                source.source_snapshot_id
            ),
            "title": (
                source.document.title
                or source.search_source.title
            ),
            "url": source.document.url,
            "canonical_url": (
                source.document.canonical_url
            ),
            "mime_type": source.document.mime_type,
            "content_hash": (
                source.document.content_hash
            ),
            "content": _source_excerpt(
                source,
                cleaned_question,
            ),
        }
        for source in sources
    ]

    request = {
        "task_question": cleaned_question,
        "objective": objective.strip(),
        "preferred_source_types": source_types or [],
        "sources": source_packet,
    }

    model = create_worker_model()
    structured_model = model.with_structured_output(
        ResearchTaskResult,
        method="json_schema",
        strict=True,
    )

    messages = [
        SystemMessage(
            content=load_prompt("researcher-v1.md")
        ),
        HumanMessage(
            content=(
                "Подготовь промежуточный результат по входному "
                "пакету. Пакет передан как JSON:\n\n"
                + json.dumps(
                    request,
                    ensure_ascii=False,
                )
            )
        ),
    ]

    if run_id is not None:
        consume_run_budget(
            run_id,
            external_requests=1,
            tokens=estimate_tokens(
                json.dumps(
                    request,
                    ensure_ascii=False,
                )
            ),
        )

    def invoke_and_validate() -> ResearchTaskResult:
        result = structured_model.invoke(messages)

        if not isinstance(result, ResearchTaskResult):
            raise TypeError(
                "Researcher returned an unexpected result type"
            )

        _validate_evidence(result, sources)
        return result

    result = retry_external_call(
        "researcher_llm",
        invoke_and_validate,
    )

    if result.task_question != cleaned_question:
        result = result.model_copy(
            update={
                "task_question": cleaned_question,
            }
        )

    return result


def persist_research_claims(
    run_id: uuid.UUID,
    task_id: uuid.UUID,
    result: ResearchTaskResult,
    sources: list[PreparedSource],
) -> list[str]:
    sources_by_url = {
        source.document.url: source
        for source in sources
    }
    claim_ids: list[str] = []

    with SessionFactory() as session:
        for finding in result.findings:
            prepared_source = sources_by_url[
                finding.source_url
            ]
            quote = finding.evidence_quote.strip()
            quote_start = (
                prepared_source.document.content.find(
                    quote
                )
            )

            if quote_start < 0:
                raise ValueError(
                    "Evidence quote disappeared before "
                    "claim persistence"
                )

            quote_end = quote_start + len(quote)
            locator = {
                "task_id": str(task_id),
                "source_url": finding.source_url,
                "quote_start": quote_start,
                "quote_end": quote_end,
            }

            if finding.locator:
                locator["description"] = (
                    finding.locator
                )

            claim = create_or_update_claim(
                session=session,
                run_id=run_id,
                research_task_id=task_id,
                source_snapshot_id=(
                    prepared_source.source_snapshot_id
                ),
                text=finding.statement,
                evidence_quote=quote,
                quote_start=quote_start,
                quote_end=quote_end,
                locator=locator,
                scope=finding.scope,
                created_by_agent="researcher-v1",
            )
            claim_ids.append(str(claim.id))

    return claim_ids


def research_task_node(
    state: ResearchWorkerState,
) -> dict:
    run_id = uuid.UUID(state["run_id"])
    task_id = uuid.UUID(state["task_id"])

    with SessionFactory() as session:
        task = get_research_task(
            session=session,
            task_id=task_id,
            run_id=run_id,
        )

        if task is None:
            raise RuntimeError(
                f"Research task not found: {task_id}"
            )

        question = task.question
        input_data = dict(task.input_data)

        update_research_task(
            session=session,
            task_id=task_id,
            status=TaskStatus.RUNNING,
        )

    try:
        search_sources = collect_search_sources(
            input_data.get(
                "search_queries",
                [],
            ),
            run_id=run_id,
        )
        prepared_sources, fetch_failures = (
            prepare_research_sources(
                run_id=run_id,
                search_sources=search_sources,
            )
        )
        result = generate_research_result(
            task_question=question,
            sources=prepared_sources,
            objective=input_data.get("objective", ""),
            source_types=input_data.get(
                "source_types",
                [],
            ),
            run_id=run_id,
        )

        if fetch_failures:
            result = result.model_copy(
                update={
                    "unanswered_questions": [
                        *result.unanswered_questions,
                        (
                            "Не удалось загрузить часть найденных "
                            "источников: "
                            + "; ".join(fetch_failures)
                        ),
                    ][:10],
                }
            )

        claim_ids = persist_research_claims(
            run_id=run_id,
            task_id=task_id,
            result=result,
            sources=prepared_sources,
        )
        output_data = {
            **result.model_dump(mode="json"),
            "source_ids": [
                str(source.source_id)
                for source in prepared_sources
            ],
            "source_snapshot_ids": [
                str(source.source_snapshot_id)
                for source in prepared_sources
            ],
            "claim_ids": claim_ids,
            "source_fetch_failures": fetch_failures,
        }

        with SessionFactory() as session:
            update_research_task(
                session=session,
                task_id=task_id,
                status=TaskStatus.COMPLETED,
                output_data=output_data,
            )

    except Exception as error:
        user_error = classify_expected_error(error)

        if user_error is None:
            logger.exception(
                "Research task failed task_id=%s",
                task_id,
            )
            output_data = {
                "error": {
                    "code": "unexpected_researcher_error",
                    "message": str(error),
                    "action": (
                        "Inspect application logs for the "
                        "failed research task."
                    ),
                    "retryable": False,
                }
            }
        else:
            logger.info(
                "Research task stopped task_id=%s error=%s",
                task_id,
                user_error.code,
            )
            output_data = {
                "error": user_error.as_dict(),
            }

        with SessionFactory() as session:
            update_research_task(
                session=session,
                task_id=task_id,
                status=TaskStatus.FAILED,
                output_data=output_data,
            )

        return {
            "claim_ids": [],
            "findings": [
                {
                    "task_id": str(task_id),
                    "result": output_data,
                }
            ],
        }

    return {
        "claim_ids": claim_ids,
        "findings": [
            {
                "task_id": str(task_id),
                "result": output_data,
            }
        ],
    }
