from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.verifier import VERIFIER_AGENT
from app.budget import consume_run_budget, estimate_tokens
from app.config import get_settings
from app.db.models import (
    ClaimReviewStatus,
    VerificationVerdict,
)
from app.db.repositories import (
    get_claim_verifications_for_run,
    get_research_run,
    get_tasks_for_run,
    upsert_research_report,
)
from app.db.session import SessionFactory
from app.models import create_writer_model
from app.prompts import load_prompt
from app.resilience import (
    ExternalOutputValidationError,
    retry_external_call,
)
from app.schemas.writer import (
    CitedStatement,
    EvidenceQualitySummary,
    FinalResearchReport,
    ReportFinding,
    ReportSource,
    WriterClaimEvidence,
    WriterDraft,
    WriterPacket,
)
from app.source_store import PROJECT_ROOT, RUNS_DIRECTORY
from app.state import ResearchState


ACCEPTED_VERDICTS = {
    VerificationVerdict.SUPPORTED,
    VerificationVerdict.PARTIALLY_SUPPORTED,
}
NUMBER_PATTERN = re.compile(
    r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?:\s?%)?"
)


def build_writer_packet(
    run_id: uuid.UUID,
) -> WriterPacket:
    with SessionFactory() as session:
        run = get_research_run(
            session=session,
            run_id=run_id,
        )

        if run is None:
            raise RuntimeError(
                f"Research run not found: {run_id}"
            )

        pairs = get_claim_verifications_for_run(
            session=session,
            run_id=run_id,
            verifier_agent=VERIFIER_AGENT,
        )
        tasks = get_tasks_for_run(
            session=session,
            run_id=run_id,
        )

    accepted: list[WriterClaimEvidence] = []
    rejected: list[WriterClaimEvidence] = []

    for claim, verification in pairs:
        snapshot = claim.source_snapshot
        source = (
            snapshot.source
            if snapshot is not None
            else None
        )
        evidence = WriterClaimEvidence(
            claim_id=str(claim.id),
            statement=claim.text,
            evidence_quote=claim.evidence_quote,
            scope=claim.scope,
            verdict=verification.verdict,
            confidence=verification.confidence,
            verification_reason=verification.reason,
            source_snapshot_id=(
                str(snapshot.id)
                if snapshot is not None
                else None
            ),
            source_url=(
                source.canonical_url
                if source is not None
                else (
                    snapshot.final_url
                    if snapshot is not None
                    else None
                )
            ),
            source_title=(
                source.title
                if source is not None
                else None
            ),
            source_publisher=(
                source.publisher
                if source is not None
                else None
            ),
            source_published_at=(
                source.published_at
                if source is not None
                else None
            ),
            source_retrieved_at=(
                snapshot.retrieved_at
                if snapshot is not None
                else None
            ),
        )

        if (
            verification.verdict in ACCEPTED_VERDICTS
            and claim.review_status
            not in {
                ClaimReviewStatus.REJECTED,
                ClaimReviewStatus.RESEARCH_REQUESTED,
            }
            and evidence.source_snapshot_id is not None
            and evidence.evidence_quote
        ):
            accepted.append(evidence)
        else:
            rejected.append(evidence)

    unanswered = list(
        dict.fromkeys(
            question.strip()
            for task in tasks
            for question in task.output_data.get(
                "unanswered_questions",
                [],
            )
            if question.strip()
        )
    )

    return WriterPacket(
        run_id=str(run_id),
        question=run.question,
        accepted_claims=accepted,
        rejected_claims=rejected,
        known_unanswered_questions=unanswered,
    )


def generate_writer_draft(
    packet: WriterPacket,
) -> WriterDraft:
    if not packet.accepted_claims:
        return WriterDraft(
            limitations=[
                (
                    "Недостаточно подтверждённых claims для "
                    "доказательного ответа."
                )
            ],
            unanswered_questions=(
                packet.known_unanswered_questions
                or [packet.question]
            ),
            contradictions=[
                CitedStatement(
                    text=(
                        "Отклонённый вывод: "
                        + claim.statement
                    ),
                    claim_ids=[claim.claim_id],
                    qualification=(
                        claim.verification_reason
                    ),
                )
                for claim in packet.rejected_claims
                if claim.verdict
                == VerificationVerdict.CONTRADICTED
            ],
        )

    model = create_writer_model()
    method = (
        "function_calling"
        if get_settings().llm_provider.strip().lower()
        == "google"
        else "json_schema"
    )
    structured_model = model.with_structured_output(
        WriterDraft,
        method=method,
        strict=True,
    )
    messages = [
        SystemMessage(
            content=load_prompt("writer-v1.md")
        ),
        HumanMessage(
            content=(
                "Подготовь финальный отчёт по provenance "
                "packet. Пакет передан как JSON:\n\n"
                + json.dumps(
                    packet.model_dump(mode="json"),
                    ensure_ascii=False,
                )
            )
        ),
    ]

    def invoke_and_validate() -> WriterDraft:
        result = structured_model.invoke(messages)

        if not isinstance(result, WriterDraft):
            raise TypeError(
                "Writer returned an unexpected result type"
            )

        try:
            validate_writer_draft(result, packet)
        except ValueError as error:
            raise ExternalOutputValidationError(
                str(error)
            ) from error

        return result

    return retry_external_call(
        "writer_llm",
        invoke_and_validate,
    )


def _statement_numbers(
    statement: CitedStatement,
) -> set[str]:
    return set(NUMBER_PATTERN.findall(statement.text))


def _evidence_numbers(
    claims: list[WriterClaimEvidence],
) -> set[str]:
    values = " ".join(
        value
        for claim in claims
        for value in (
            claim.statement,
            claim.evidence_quote or "",
            claim.scope or "",
        )
    )

    return set(NUMBER_PATTERN.findall(values))


def validate_writer_draft(
    draft: WriterDraft,
    packet: WriterPacket,
) -> None:
    accepted_by_id = {
        claim.claim_id: claim
        for claim in packet.accepted_claims
    }
    rejected_by_id = {
        claim.claim_id: claim
        for claim in packet.rejected_claims
    }
    all_packet_numbers = _evidence_numbers(
        [
            *packet.accepted_claims,
            *packet.rejected_claims,
        ]
    ) | set(
        NUMBER_PATTERN.findall(
            " ".join(
                [
                    packet.question,
                    *packet.known_unanswered_questions,
                ]
            )
        )
    )
    main_statements = [
        *(
            [draft.direct_answer]
            if draft.direct_answer is not None
            else []
        ),
        *[
            finding.statement
            for finding in draft.key_findings
        ],
        *draft.short_answer,
        *[
            statement
            for section in draft.sections
            for statement in section.statements
        ],
    ]

    if packet.accepted_claims and not main_statements:
        raise ValueError(
            "Writer omitted all accepted claims"
        )

    for statement in main_statements:
        unknown_ids = (
            set(statement.claim_ids)
            - set(accepted_by_id)
        )

        if unknown_ids:
            raise ValueError(
                "Writer used rejected or unknown claims "
                "in the main report: "
                + ", ".join(sorted(unknown_ids))
            )

        cited_claims = [
            accepted_by_id[claim_id]
            for claim_id in statement.claim_ids
        ]

        if (
            any(
                claim.verdict
                == VerificationVerdict.PARTIALLY_SUPPORTED
                for claim in cited_claims
            )
            and not statement.qualification
        ):
            raise ValueError(
                "Partially supported statement has no "
                "qualification"
            )

        missing_numbers = (
            _statement_numbers(statement)
            - _evidence_numbers(cited_claims)
        )

        if missing_numbers:
            raise ValueError(
                "Writer introduced unsupported numeric values: "
                + ", ".join(sorted(missing_numbers))
            )

        for rejected in packet.rejected_claims:
            if (
                rejected.statement.casefold()
                in statement.text.casefold()
                and all(
                    accepted.statement.casefold()
                    != rejected.statement.casefold()
                    for accepted in cited_claims
                )
            ):
                raise ValueError(
                    "Writer copied a rejected claim into "
                    "the main report"
                )

    for statement in draft.contradictions:
        unknown_ids = (
            set(statement.claim_ids)
            - set(rejected_by_id)
        )

        if unknown_ids:
            raise ValueError(
                "Contradiction references accepted or unknown "
                "claims: "
                + ", ".join(sorted(unknown_ids))
            )

    required_contradictions = {
        claim.claim_id
        for claim in packet.rejected_claims
        if claim.verdict
        == VerificationVerdict.CONTRADICTED
    }
    cited_contradictions = {
        claim_id
        for statement in draft.contradictions
        for claim_id in statement.claim_ids
    }
    missing_contradictions = (
        required_contradictions
        - cited_contradictions
    )

    if missing_contradictions:
        raise ValueError(
            "Writer omitted contradicted claims: "
            + ", ".join(
                sorted(missing_contradictions)
            )
        )

    free_form_values = [
        *draft.limitations,
        *draft.unanswered_questions,
        *[
            statement.qualification or ""
            for statement in [
                *main_statements,
                *draft.contradictions,
            ]
        ],
        *[
            statement.text
            for statement in draft.contradictions
        ],
    ]

    for value in free_form_values:
        missing_numbers = (
            set(NUMBER_PATTERN.findall(value))
            - all_packet_numbers
        )

        if missing_numbers:
            raise ValueError(
                "Writer introduced unsupported numeric values "
                "outside the main report: "
                + ", ".join(sorted(missing_numbers))
            )


def finalize_report(
    draft: WriterDraft,
    packet: WriterPacket,
) -> FinalResearchReport:
    claims_by_id = {
        claim.claim_id: claim
        for claim in [
            *packet.accepted_claims,
            *packet.rejected_claims,
        ]
    }
    direct_answer = (
        draft.direct_answer
        or (
            draft.short_answer[0]
            if draft.short_answer
            else None
        )
    )
    key_findings = (
        draft.key_findings
        or [
            ReportFinding(
                title=f"Ключевой вывод {index}",
                statement=statement,
            )
            for index, statement in enumerate(
                draft.short_answer[1:],
                start=1,
            )
        ]
    )
    cited_ids = list(
        dict.fromkeys(
            claim_id
            for statement in [
                *(
                    [direct_answer]
                    if direct_answer is not None
                    else []
                ),
                *[
                    finding.statement
                    for finding in key_findings
                ],
                *draft.short_answer,
                *[
                    statement
                    for section in draft.sections
                    for statement in section.statements
                ],
                *draft.contradictions,
            ]
            for claim_id in statement.claim_ids
        )
    )
    sources = [
        ReportSource(
            citation_label=f"C{index}",
            claim_id=claim_id,
            source_snapshot_id=(
                claims_by_id[claim_id].source_snapshot_id
            ),
            source_url=claims_by_id[claim_id].source_url,
            source_title=(
                claims_by_id[claim_id].source_title
            ),
            source_publisher=(
                claims_by_id[claim_id].source_publisher
            ),
            source_published_at=(
                claims_by_id[claim_id].source_published_at
            ),
            source_retrieved_at=(
                claims_by_id[claim_id].source_retrieved_at
            ),
            evidence_quote=(
                claims_by_id[claim_id].evidence_quote
            ),
            verdict=claims_by_id[claim_id].verdict,
            confidence=claims_by_id[claim_id].confidence,
            verification_reason=(
                claims_by_id[claim_id].verification_reason
            ),
        )
        for index, claim_id in enumerate(
            cited_ids,
            start=1,
        )
    ]
    accepted_cited = [
        claims_by_id[claim_id]
        for claim_id in cited_ids
        if claim_id
        in {
            claim.claim_id
            for claim in packet.accepted_claims
        }
    ]
    overall_confidence = (
        sum(
            claim.confidence
            for claim in accepted_cited
        )
        / len(accepted_cited)
        if accepted_cited
        else 0.0
    )
    limitations = list(
        dict.fromkeys(
            [
                *draft.limitations,
                *[
                    (
                        "Частично подтверждённый claim "
                        f"{claim.claim_id}: "
                        f"{claim.verification_reason}"
                    )
                    for claim in packet.accepted_claims
                    if claim.verdict
                    == VerificationVerdict.PARTIALLY_SUPPORTED
                ],
                *[
                    (
                        "Отклонённый claim "
                        f"{claim.claim_id} "
                        f"({claim.verdict.value}): "
                        f"{claim.verification_reason}"
                    )
                    for claim in packet.rejected_claims
                    if claim.verdict
                    != VerificationVerdict.CONTRADICTED
                ],
            ]
        )
    )
    unanswered = list(
        dict.fromkeys(
            [
                *packet.known_unanswered_questions,
                *draft.unanswered_questions,
            ]
        )
    )
    cited_claims = [
        claims_by_id[claim_id]
        for claim_id in cited_ids
    ]
    unsupported_verdicts = {
        VerificationVerdict.OUT_OF_SCOPE,
        VerificationVerdict.SOURCE_UNAVAILABLE,
        VerificationVerdict.CITATION_MISMATCH,
        VerificationVerdict.INSUFFICIENT_EVIDENCE,
    }
    quality_summary = EvidenceQualitySummary(
        confirmed_claims=sum(
            claim.verdict == VerificationVerdict.SUPPORTED
            for claim in cited_claims
        ),
        limited_claims=sum(
            claim.verdict
            == VerificationVerdict.PARTIALLY_SUPPORTED
            for claim in cited_claims
        ),
        contradicted_claims=sum(
            claim.verdict == VerificationVerdict.CONTRADICTED
            for claim in cited_claims
        ),
        unsupported_claims=sum(
            claim.verdict in unsupported_verdicts
            for claim in cited_claims
        ),
        source_count=len(
            {
                (
                    claim.source_snapshot_id
                    or claim.source_url
                )
                for claim in cited_claims
                if (
                    claim.source_snapshot_id
                    or claim.source_url
                )
            }
        ),
        overall_confidence=round(
            overall_confidence,
            4,
        ),
        caveats=list(
            dict.fromkeys(
                [
                    *limitations,
                    *unanswered,
                ]
            )
        ),
    )

    return FinalResearchReport(
        run_id=packet.run_id,
        question=packet.question,
        direct_answer=direct_answer,
        key_findings=key_findings,
        short_answer=draft.short_answer,
        sections=draft.sections,
        limitations=limitations,
        contradictions=draft.contradictions,
        unanswered_questions=unanswered,
        sources=sources,
        overall_confidence=round(
            overall_confidence,
            4,
        ),
        quality_summary=quality_summary,
    )


def _render_cited_statement(
    statement: CitedStatement,
    sources_by_claim: dict[str, ReportSource],
) -> str:
    citations = []

    for claim_id in statement.claim_ids:
        source = sources_by_claim[claim_id]

        if source.source_url:
            citations.append(
                f"[{source.citation_label}]"
                f"({source.source_url})"
            )
        else:
            citations.append(
                f"[{source.citation_label}]"
            )

    value = (
        statement.text.rstrip()
        + " "
        + " ".join(citations)
    )

    if statement.qualification:
        value += (
            "\n\n"
            f"_Оговорка: {statement.qualification}_"
        )

    return value


def render_report_markdown(
    report: FinalResearchReport,
) -> str:
    sources_by_claim = {
        source.claim_id: source
        for source in report.sources
    }
    lines = [
        "# Research Report",
        "",
        f"**Вопрос:** {report.question}",
        "",
        (
            "**Общая уверенность:** "
            f"{report.overall_confidence:.0%}"
        ),
        "",
        "## Краткий ответ",
        "",
    ]

    if report.direct_answer:
        lines.extend(
            [
                _render_cited_statement(
                    report.direct_answer,
                    sources_by_claim,
                ),
                "",
            ]
        )
    elif report.short_answer:
        lines.extend(
            [
                _render_cited_statement(
                    report.short_answer[0],
                    sources_by_claim,
                ),
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Подтверждённых данных недостаточно "
                "для краткого ответа.",
                "",
            ]
        )

    if report.key_findings:
        lines.extend(["## Ключевые выводы", ""])

        for finding in report.key_findings:
            lines.extend(
                [
                    f"### {finding.title}",
                    "",
                    _render_cited_statement(
                        finding.statement,
                        sources_by_claim,
                    ),
                    "",
                ]
            )

    for section in report.sections:
        lines.extend(
            [
                f"## {section.heading}",
                "",
            ]
        )

        for statement in section.statements:
            lines.extend(
                [
                    _render_cited_statement(
                        statement,
                        sources_by_claim,
                    ),
                    "",
                ]
            )

    note_sections = [
        ("Ограничения", report.limitations),
        (
            "Противоречия и отклонённые выводы",
            [
                _render_cited_statement(
                    statement,
                    sources_by_claim,
                )
                for statement in report.contradictions
            ],
        ),
        (
            "Неотвеченные вопросы",
            report.unanswered_questions,
        ),
    ]

    for heading, items in note_sections:
        if not items:
            continue

        lines.extend([f"## {heading}", ""])

        for item in items:
            lines.extend([f"- {item}", ""])

    lines.extend(["## Источники", ""])

    if not report.sources:
        lines.extend(["Источники не использованы.", ""])

    for source in report.sources:
        title = (
            source.source_title
            or source.source_url
            or "Недоступный источник"
        )
        link = (
            f"[{title}]({source.source_url})"
            if source.source_url
            else title
        )
        lines.extend(
            [
                (
                    f"- **{source.citation_label}** — {link}; "
                    f"`claim_id={source.claim_id}`; "
                    "`source_snapshot_id="
                    f"{source.source_snapshot_id or 'none'}`"
                ),
                (
                    f"  > {source.evidence_quote}"
                    if source.evidence_quote
                    else "  > Цитата недоступна."
                ),
                (
                    "  "
                    f"Проверка: {source.verdict.value}, "
                    f"уверенность {source.confidence:.0%}. "
                    f"{source.verification_reason}"
                ),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def persist_report_artifacts(
    run_id: uuid.UUID,
    report: FinalResearchReport,
    markdown: str,
    *,
    runs_directory: Path = RUNS_DIRECTORY,
) -> None:
    run_directory = runs_directory / str(run_id)
    run_directory.mkdir(parents=True, exist_ok=True)
    markdown_path = run_directory / "report.md"
    json_path = run_directory / "report.json"
    json_text = (
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    json_tmp = json_path.with_suffix(".json.tmp")
    markdown_tmp.write_text(markdown, encoding="utf-8")
    json_tmp.write_text(json_text, encoding="utf-8")
    markdown_tmp.replace(markdown_path)
    json_tmp.replace(json_path)

    def relative_path(path: Path) -> str:
        try:
            return str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(path)

    with SessionFactory() as session:
        upsert_research_report(
            session=session,
            run_id=run_id,
            markdown_path=relative_path(markdown_path),
            json_path=relative_path(json_path),
            markdown_hash=hashlib.sha256(
                markdown.encode("utf-8")
            ).hexdigest(),
            json_hash=hashlib.sha256(
                json_text.encode("utf-8")
            ).hexdigest(),
            result_json=report.model_dump(mode="json"),
        )


def writer_node(state: ResearchState) -> dict:
    run_id = uuid.UUID(state["run_id"])
    packet = build_writer_packet(run_id)

    if packet.accepted_claims:
        consume_run_budget(
            run_id,
            external_requests=1,
            tokens=estimate_tokens(
                json.dumps(
                    packet.model_dump(mode="json"),
                    ensure_ascii=False,
                )
            ),
        )

    draft = generate_writer_draft(packet)
    validate_writer_draft(draft, packet)
    report = finalize_report(draft, packet)
    markdown = render_report_markdown(report)
    persist_report_artifacts(
        run_id=run_id,
        report=report,
        markdown=markdown,
    )

    return {
        "report": markdown,
        "report_json": report.model_dump(mode="json"),
    }
