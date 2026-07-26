from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from app.agents.verifier import VERIFIER_AGENT
from app.db.models import (
    ClaimReviewStatus,
    ReportReviewStatus,
    ResearchTask,
    ReviewDecisionType,
    TaskStatus,
)
from app.db.repositories import (
    get_claim,
    get_claim_verifications_for_run,
    get_research_report,
    get_research_run,
    record_claim_review,
    record_report_review,
)
from app.db.session import SessionFactory
from app.source_store import PROJECT_ROOT
from app.rbac import authorize


def _required_text(value: str, name: str) -> str:
    cleaned = value.strip()

    if not cleaned:
        raise ValueError(f"{name} must not be empty")

    return cleaned


def review_claim(
    claim_id: uuid.UUID,
    *,
    decision: ReviewDecisionType,
    reason: str,
    reviewer: str,
) -> uuid.UUID:
    reason = _required_text(reason, "reason")
    reviewer = _required_text(reviewer, "reviewer")

    with SessionFactory() as session:
        identity = authorize(
            session,
            reviewer,
            "review_claim",
        )
        entry = record_claim_review(
            session,
            claim_id,
            decision=decision,
            reason=reason,
            reviewer=reviewer,
            reviewer_identity_id=identity.id,
        )
        return entry.run_id


def request_additional_research(
    claim_id: uuid.UUID,
    *,
    reason: str,
    reviewer: str,
) -> ResearchTask:
    reason = _required_text(reason, "reason")
    reviewer = _required_text(reviewer, "reviewer")

    with SessionFactory() as session:
        identity = authorize(
            session,
            reviewer,
            "review_claim",
        )
        claim = get_claim(session, claim_id)

        if claim is None:
            raise RuntimeError(f"Claim not found: {claim_id}")

        record_claim_review(
            session,
            claim_id,
            decision=ReviewDecisionType.REQUEST_RESEARCH,
            reason=reason,
            reviewer=reviewer,
            reviewer_identity_id=identity.id,
        )
        task = ResearchTask(
            run_id=claim.run_id,
            task_type="human_followup_research",
            question=(
                "Найди дополнительные доказательства для "
                f"утверждения: {claim.text}"
            ),
            status=TaskStatus.PENDING,
            priority=0,
            assigned_agent=None,
            input_data={
                "title": "Human-requested follow-up",
                "objective": reason,
                "source_types": [],
                "search_queries": [claim.text],
                "origin_claim_id": str(claim.id),
                "requested_by": reviewer,
            },
            output_data={},
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def review_report(
    run_id: uuid.UUID,
    *,
    decision: ReviewDecisionType,
    reason: str,
    reviewer: str,
) -> None:
    reason = _required_text(reason, "reason")
    reviewer = _required_text(reviewer, "reviewer")

    with SessionFactory() as session:
        identity = authorize(
            session,
            reviewer,
            "review_report",
        )
        record_report_review(
            session,
            run_id,
            decision=decision,
            reason=reason,
            reviewer=reviewer,
            reviewer_identity_id=identity.id,
        )


def _artifact_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _verified_artifact(path: Path, expected_hash: str) -> bytes:
    content = path.read_bytes()
    actual_hash = hashlib.sha256(content).hexdigest()

    if actual_hash != expected_hash:
        raise RuntimeError(
            f"Report artifact hash mismatch: {path}"
        )

    return content


def publish_report(
    run_id: uuid.UUID,
    *,
    reason: str,
    reviewer: str,
) -> tuple[Path, Path]:
    with SessionFactory() as session:
        identity = authorize(
            session,
            reviewer,
            "publish",
        )
        report = get_research_report(session, run_id)

        if report is None:
            raise RuntimeError(
                f"Research report not found for run: {run_id}"
            )

        if report.review_status not in {
            ReportReviewStatus.APPROVED,
            ReportReviewStatus.PUBLISHED,
        }:
            raise RuntimeError(
                "Publication gate blocked the report: "
                "human approval is required"
            )

        markdown_path = _artifact_path(
            report.markdown_path
        )
        json_path = _artifact_path(report.json_path)
        markdown = _verified_artifact(
            markdown_path,
            report.markdown_hash,
        )
        json_content = _verified_artifact(
            json_path,
            report.json_hash,
        )

        published_markdown = (
            markdown_path.parent / "published.md"
        )
        published_json = (
            json_path.parent / "published.json"
        )
        markdown_tmp = published_markdown.with_suffix(
            ".md.tmp"
        )
        json_tmp = published_json.with_suffix(".json.tmp")
        markdown_tmp.write_bytes(markdown)
        json_tmp.write_bytes(json_content)
        markdown_tmp.replace(published_markdown)
        json_tmp.replace(published_json)

        record_report_review(
            session,
            run_id,
            decision=ReviewDecisionType.PUBLISH,
            reason=_required_text(reason, "reason"),
            reviewer=_required_text(reviewer, "reviewer"),
            reviewer_identity_id=identity.id,
        )

    return published_markdown, published_json


def render_obsidian_note(
    *,
    run_id: uuid.UUID,
    question: str,
    claims: list[dict],
) -> str:
    lines = [
        "---",
        f'research_run: "{run_id}"',
        "review_status: approved",
        "tags:",
        "  - deep-research",
        "  - verified",
        "---",
        "",
        f"# {question}",
        "",
        "## Проверенные материалы",
        "",
    ]

    if not claims:
        lines.extend(
            [
                "Нет одобренных claims для экспорта.",
                "",
            ]
        )

    for item in claims:
        lines.extend(
            [
                f"### {item['text']}",
                "",
                (
                    f"- Verdict: `{item['verdict']}` "
                    f"({item['confidence']:.0%})"
                ),
                f"- Источник: {item['source_url']}",
                (
                    "- Snapshot: "
                    f"`{item['source_snapshot_id']}`"
                ),
                f"- Claim: `{item['claim_id']}`",
                "",
                f"> {item['evidence_quote']}",
                "",
                f"_Human review: {item['review_status']}_",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def export_to_obsidian(
    run_id: uuid.UUID,
    vault_directory: Path,
    *,
    reviewer: str,
) -> tuple[Path, Path]:
    with SessionFactory() as session:
        authorize(session, reviewer, "export")
        run = get_research_run(session, run_id)
        report = get_research_report(session, run_id)

        if run is None:
            raise RuntimeError(
                f"Research run not found: {run_id}"
            )

        if (
            report is None
            or report.review_status
            not in {
                ReportReviewStatus.APPROVED,
                ReportReviewStatus.PUBLISHED,
            }
        ):
            raise RuntimeError(
                "Obsidian export blocked: approved report "
                "is required"
            )

        pairs = get_claim_verifications_for_run(
            session,
            run_id,
            verifier_agent=VERIFIER_AGENT,
        )
        claims = []

        for claim, verification in pairs:
            if (
                claim.review_status
                != ClaimReviewStatus.APPROVED
            ):
                continue

            snapshot = claim.source_snapshot
            source = (
                snapshot.source
                if snapshot is not None
                else None
            )
            claims.append(
                {
                    "claim_id": str(claim.id),
                    "text": claim.text,
                    "evidence_quote": (
                        claim.evidence_quote or ""
                    ),
                    "review_status": (
                        claim.review_status.value
                    ),
                    "verdict": verification.verdict.value,
                    "confidence": verification.confidence,
                    "source_snapshot_id": (
                        str(snapshot.id)
                        if snapshot is not None
                        else "none"
                    ),
                    "source_url": (
                        source.canonical_url
                        if source is not None
                        else (
                            snapshot.final_url
                            if snapshot is not None
                            else "unavailable"
                        )
                    ),
                }
            )

        question = run.question

    target_directory = vault_directory.resolve()
    target_directory.mkdir(parents=True, exist_ok=True)
    note_path = (
        target_directory
        / f"deep-research-{run_id}.md"
    )
    json_path = (
        target_directory
        / f"deep-research-{run_id}.json"
    )
    note = render_obsidian_note(
        run_id=run_id,
        question=question,
        claims=claims,
    )
    json_text = (
        json.dumps(
            {
                "run_id": str(run_id),
                "question": question,
                "claims": claims,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    note_tmp = note_path.with_suffix(".md.tmp")
    json_tmp = json_path.with_suffix(".json.tmp")
    note_tmp.write_text(note, encoding="utf-8")
    json_tmp.write_text(json_text, encoding="utf-8")
    note_tmp.replace(note_path)
    json_tmp.replace(json_path)
    return note_path, json_path
