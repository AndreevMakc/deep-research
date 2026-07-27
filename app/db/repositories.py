import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.budget import RunLimitExceeded
from app.db.models import (
    Claim,
    ClaimReviewStatus,
    ClaimStatus,
    OperationalEvent,
    ReportReviewStatus,
    ReviewDecision,
    ReviewDecisionType,
    ReviewTargetType,
    ResearchReport,
    ResearchRun,
    ResearchTask,
    RunStatus,
    Source,
    SourceSnapshot,
    TaskStatus,
    Verification,
    VerificationVerdict,
)
from app.library import generate_run_title


def create_research_run(
    session: Session,
    question: str,
    *,
    limits: dict[str, int] | None = None,
    tenant_id: uuid.UUID | None = None,
) -> ResearchRun:
    limits = limits or {}
    run = ResearchRun(
        question=question,
        title=generate_run_title(question),
        status=RunStatus.CREATED,
        tenant_id=tenant_id,
        max_external_requests=limits.get(
            "max_external_requests",
            100,
        ),
        max_sources=limits.get("max_sources", 50),
        max_claims=limits.get("max_claims", 100),
        max_tokens=limits.get("max_tokens", 200_000),
        max_run_seconds=limits.get(
            "max_run_seconds",
            3_600,
        ),
    )

    session.add(run)
    session.commit()
    session.refresh(run)

    return run


def get_research_run(
    session: Session,
    run_id: uuid.UUID,
) -> ResearchRun | None:
    statement = select(ResearchRun).where(
        ResearchRun.id == run_id
    )

    return session.scalar(statement)


def list_research_runs(
    session: Session,
    *,
    limit: int = 20,
) -> list[ResearchRun]:
    statement = (
        select(ResearchRun)
        .order_by(ResearchRun.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    return list(session.scalars(statement).all())


def create_research_tasks(
    session: Session,
    run_id: uuid.UUID,
    tasks: list[dict],
) -> list[ResearchTask]:
    records: list[ResearchTask] = []

    for task in tasks:
        record = ResearchTask(
            run_id=run_id,
            task_type="web_research",
            question=task["question"],
            status=TaskStatus.PENDING,
            priority=task["priority"],
            assigned_agent=None,
            input_data={
                "title": task["title"],
                "objective": task["objective"],
                "source_types": task["source_types"],
                "search_queries": task["search_queries"],
            },
            output_data={},
        )

        session.add(record)
        records.append(record)

    session.commit()

    for record in records:
        session.refresh(record)

    return records


def get_tasks_for_run(
    session: Session,
    run_id: uuid.UUID,
) -> list[ResearchTask]:
    statement = (
        select(ResearchTask)
        .where(ResearchTask.run_id == run_id)
        .order_by(
            ResearchTask.priority,
            ResearchTask.created_at,
        )
    )

    return list(
        session.scalars(statement).all()
    )


def get_research_task(
    session: Session,
    task_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
) -> ResearchTask | None:
    statement = select(ResearchTask).where(
        ResearchTask.id == task_id
    )

    if run_id is not None:
        statement = statement.where(
            ResearchTask.run_id == run_id
        )

    return session.scalar(statement)


def update_research_task(
    session: Session,
    task_id: uuid.UUID,
    status: TaskStatus,
    output_data: dict | None = None,
) -> ResearchTask:
    task = session.get(ResearchTask, task_id)

    if task is None:
        raise RuntimeError(
            f"Research task not found: {task_id}"
        )

    task.status = status

    if output_data is not None:
        task.output_data = output_data

    session.commit()
    session.refresh(task)

    return task


def upsert_source(
    session: Session,
    run_id: uuid.UUID,
    *,
    url: str,
    canonical_url: str,
    title: str | None,
) -> Source:
    existing_statement = select(Source).where(
        Source.run_id == run_id,
        Source.canonical_url == canonical_url,
    )
    existing = session.scalar(existing_statement)

    if existing is not None:
        existing.url = url
        existing.title = title
        session.commit()
        session.refresh(existing)
        return existing

    run = session.scalar(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .with_for_update()
    )

    if run is None:
        raise RuntimeError(f"Research run not found: {run_id}")

    existing = session.scalar(existing_statement)

    if existing is not None:
        existing.url = url
        existing.title = title
        session.commit()
        session.refresh(existing)
        return existing

    source_count = session.scalar(
        select(func.count(Source.id)).where(
            Source.run_id == run_id
        )
    ) or 0

    if source_count >= run.max_sources:
        raise RunLimitExceeded(
            "Run source limit exceeded: "
            f"{source_count}/{run.max_sources}"
        )

    statement = (
        insert(Source)
        .values(
            run_id=run_id,
            url=url,
            canonical_url=canonical_url,
            title=title,
        )
        .on_conflict_do_update(
            constraint="uq_source_run_url",
            set_={
                "url": url,
                "title": title,
            },
        )
        .returning(Source)
    )

    source = session.scalars(statement).one()
    session.commit()

    return source


def create_source_snapshot(
    session: Session,
    run_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    final_url: str,
    content_hash: str,
    mime_type: str,
    local_path: str,
    http_status: int | None,
    content_length: int,
    metadata_json: dict,
) -> SourceSnapshot:
    statement = (
        insert(SourceSnapshot)
        .values(
            run_id=run_id,
            source_id=source_id,
            final_url=final_url,
            content_hash=content_hash,
            mime_type=mime_type,
            local_path=local_path,
            http_status=http_status,
            content_length=content_length,
            metadata_json=metadata_json,
        )
        .on_conflict_do_nothing(
            constraint="uq_source_snapshot_hash",
        )
        .returning(SourceSnapshot)
    )
    snapshot = session.scalars(
        statement
    ).one_or_none()

    if snapshot is None:
        existing_statement = select(
            SourceSnapshot
        ).where(
            SourceSnapshot.source_id == source_id,
            SourceSnapshot.content_hash == content_hash,
        )
        snapshot = session.scalar(existing_statement)

    if snapshot is None:
        raise RuntimeError(
            "Failed to create or load source snapshot"
        )

    session.commit()

    return snapshot


def create_or_update_claim(
    session: Session,
    run_id: uuid.UUID,
    research_task_id: uuid.UUID,
    source_snapshot_id: uuid.UUID,
    *,
    text: str,
    evidence_quote: str,
    quote_start: int,
    quote_end: int,
    locator: dict,
    scope: str | None,
    created_by_agent: str,
) -> Claim:
    statement = select(Claim).where(
        Claim.run_id == run_id,
        Claim.research_task_id == research_task_id,
        Claim.source_snapshot_id
        == source_snapshot_id,
        Claim.text == text,
    )
    claim = session.scalar(statement)

    if claim is None:
        run = session.scalar(
            select(ResearchRun)
            .where(ResearchRun.id == run_id)
            .with_for_update()
        )

        if run is None:
            raise RuntimeError(
                f"Research run not found: {run_id}"
            )

        claim = session.scalar(statement)

    if claim is None:
        claim_count = session.scalar(
            select(func.count(Claim.id)).where(
                Claim.run_id == run_id
            )
        ) or 0

        if claim_count >= run.max_claims:
            raise RunLimitExceeded(
                "Run claim limit exceeded: "
                f"{claim_count}/{run.max_claims}"
            )

        claim = Claim(
            run_id=run_id,
            research_task_id=research_task_id,
            source_snapshot_id=source_snapshot_id,
            text=text,
            created_by_agent=created_by_agent,
        )
        session.add(claim)

    claim.evidence_quote = evidence_quote
    claim.quote_start = quote_start
    claim.quote_end = quote_end
    claim.locator = locator
    claim.scope = scope
    session.commit()
    session.refresh(claim)

    return claim


def get_claim(
    session: Session,
    claim_id: uuid.UUID,
) -> Claim | None:
    statement = (
        select(Claim)
        .options(
            joinedload(Claim.source_snapshot).joinedload(
                SourceSnapshot.source
            )
        )
        .where(Claim.id == claim_id)
    )

    return session.scalar(statement)


def create_verification(
    session: Session,
    claim_id: uuid.UUID,
    *,
    verifier_agent: str,
    verdict: VerificationVerdict,
    confidence: float,
    reason: str,
    checked_source_ids: list[str],
) -> Verification:
    claim = session.get(Claim, claim_id)

    if claim is None:
        raise RuntimeError(f"Claim not found: {claim_id}")

    statement = (
        insert(Verification)
        .values(
            claim_id=claim_id,
            verifier_agent=verifier_agent,
            verdict=verdict,
            confidence=confidence,
            reason=reason,
            checked_source_ids=checked_source_ids,
        )
        .on_conflict_do_update(
            constraint="uq_verification_claim_agent",
            set_={
                "verdict": verdict,
                "confidence": confidence,
                "reason": reason,
                "checked_source_ids": checked_source_ids,
            },
        )
        .returning(Verification)
    )
    verification = session.scalars(statement).one()
    claim.status = ClaimStatus(verdict.value)
    session.commit()

    return verification


def get_claims_for_run(
    session: Session,
    run_id: uuid.UUID,
) -> list[Claim]:
    statement = (
        select(Claim)
        .where(Claim.run_id == run_id)
        .order_by(Claim.created_at, Claim.id)
    )

    return list(session.scalars(statement).all())


def get_research_report(
    session: Session,
    run_id: uuid.UUID,
) -> ResearchReport | None:
    return session.scalar(
        select(ResearchReport).where(
            ResearchReport.run_id == run_id
        )
    )


def get_review_decisions_for_run(
    session: Session,
    run_id: uuid.UUID,
) -> list[ReviewDecision]:
    statement = (
        select(ReviewDecision)
        .where(ReviewDecision.run_id == run_id)
        .order_by(
            ReviewDecision.created_at,
            ReviewDecision.id,
        )
    )
    return list(session.scalars(statement).all())


def get_operational_events_for_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    limit: int = 200,
) -> list[OperationalEvent]:
    statement = (
        select(OperationalEvent)
        .where(OperationalEvent.run_id == run_id)
        .order_by(
            OperationalEvent.created_at,
            OperationalEvent.id,
        )
        .limit(max(1, min(limit, 5_000)))
    )
    return list(session.scalars(statement).all())


def record_claim_review(
    session: Session,
    claim_id: uuid.UUID,
    *,
    decision: ReviewDecisionType,
    reason: str,
    reviewer: str,
    reviewer_identity_id: uuid.UUID,
) -> ReviewDecision:
    claim = session.get(Claim, claim_id)

    if claim is None:
        raise RuntimeError(f"Claim not found: {claim_id}")

    status_by_decision = {
        ReviewDecisionType.APPROVE: (
            ClaimReviewStatus.APPROVED
        ),
        ReviewDecisionType.REJECT: (
            ClaimReviewStatus.REJECTED
        ),
        ReviewDecisionType.REQUEST_RESEARCH: (
            ClaimReviewStatus.RESEARCH_REQUESTED
        ),
    }

    if decision not in status_by_decision:
        raise ValueError(
            f"Unsupported claim review decision: {decision.value}"
        )

    claim.review_status = status_by_decision[decision]
    report = get_research_report(session, claim.run_id)

    if report is not None:
        report.review_status = ReportReviewStatus.PENDING
        report.approved_at = None
        report.published_at = None

    entry = ReviewDecision(
        run_id=claim.run_id,
        target_type=ReviewTargetType.CLAIM,
        target_id=claim.id,
        decision=decision,
        reason=reason,
        reviewer=reviewer,
        reviewer_identity_id=reviewer_identity_id,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def record_report_review(
    session: Session,
    run_id: uuid.UUID,
    *,
    decision: ReviewDecisionType,
    reason: str,
    reviewer: str,
    reviewer_identity_id: uuid.UUID,
) -> ReviewDecision:
    report = get_research_report(session, run_id)

    if report is None:
        raise RuntimeError(
            f"Research report not found for run: {run_id}"
        )

    if decision == ReviewDecisionType.APPROVE:
        referenced_ids = {
            uuid.UUID(source["claim_id"])
            for source in report.result_json.get(
                "sources",
                [],
            )
            if source.get("claim_id")
        }
        referenced_claims = (
            list(
                session.scalars(
                    select(Claim).where(
                        Claim.id.in_(referenced_ids)
                    )
                ).all()
            )
            if referenced_ids
            else []
        )
        approved_ids = {
            claim.id
            for claim in referenced_claims
            if claim.review_status
            == ClaimReviewStatus.APPROVED
        }
        unresolved = referenced_ids - approved_ids

        if unresolved:
            raise RuntimeError(
                "Report approval blocked: review all "
                "referenced claims first: "
                + ", ".join(
                    str(value)
                    for value in sorted(
                        unresolved,
                        key=str,
                    )
                )
            )

        report.review_status = ReportReviewStatus.APPROVED
        report.approved_at = func.now()
        report.published_at = None
    elif decision == ReviewDecisionType.REJECT:
        report.review_status = ReportReviewStatus.REJECTED
        report.approved_at = None
        report.published_at = None
    elif decision == ReviewDecisionType.PUBLISH:
        if report.review_status not in {
            ReportReviewStatus.APPROVED,
            ReportReviewStatus.PUBLISHED,
        }:
            raise RuntimeError(
                "Publication gate blocked the report: "
                "human approval is required"
            )

        report.review_status = ReportReviewStatus.PUBLISHED
        report.published_at = func.now()
    else:
        raise ValueError(
            f"Unsupported report review decision: {decision.value}"
        )

    entry = ReviewDecision(
        run_id=run_id,
        target_type=ReviewTargetType.REPORT,
        target_id=report.id,
        decision=decision,
        reason=reason,
        reviewer=reviewer,
        reviewer_identity_id=reviewer_identity_id,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def get_claim_ids_needing_verification(
    session: Session,
    run_id: uuid.UUID,
    claim_ids: list[uuid.UUID],
    *,
    verifier_agent: str,
) -> list[uuid.UUID]:
    if not claim_ids:
        return []

    existing_verification = (
        select(Verification.id)
        .where(
            Verification.claim_id == Claim.id,
            Verification.verifier_agent
            == verifier_agent,
        )
        .exists()
    )
    statement = (
        select(Claim.id)
        .where(
            Claim.run_id == run_id,
            Claim.id.in_(claim_ids),
            ~existing_verification,
        )
        .order_by(Claim.created_at, Claim.id)
    )

    return list(session.scalars(statement).all())


def get_verifications_for_claims(
    session: Session,
    claim_ids: list[uuid.UUID],
    *,
    verifier_agent: str,
) -> list[Verification]:
    if not claim_ids:
        return []

    statement = (
        select(Verification)
        .where(
            Verification.claim_id.in_(claim_ids),
            Verification.verifier_agent
            == verifier_agent,
        )
        .order_by(
            Verification.created_at,
            Verification.id,
        )
    )

    return list(session.scalars(statement).all())


def get_claim_verifications_for_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    verifier_agent: str,
) -> list[tuple[Claim, Verification]]:
    statement = (
        select(Claim, Verification)
        .join(
            Verification,
            Verification.claim_id == Claim.id,
        )
        .options(
            joinedload(Claim.source_snapshot).joinedload(
                SourceSnapshot.source
            )
        )
        .where(
            Claim.run_id == run_id,
            Verification.verifier_agent
            == verifier_agent,
        )
        .order_by(Claim.created_at, Claim.id)
    )

    return [
        (claim, verification)
        for claim, verification in session.execute(
            statement
        ).all()
    ]


def upsert_research_report(
    session: Session,
    run_id: uuid.UUID,
    *,
    markdown_path: str,
    json_path: str,
    markdown_hash: str,
    json_hash: str,
    result_json: dict,
) -> ResearchReport:
    statement = (
        insert(ResearchReport)
        .values(
            run_id=run_id,
            markdown_path=markdown_path,
            json_path=json_path,
            markdown_hash=markdown_hash,
            json_hash=json_hash,
            result_json=result_json,
        )
        .on_conflict_do_update(
            constraint="uq_research_report_run",
            set_={
                "markdown_path": markdown_path,
                "json_path": json_path,
                "markdown_hash": markdown_hash,
                "json_hash": json_hash,
                "result_json": result_json,
                "review_status": (
                    ReportReviewStatus.PENDING
                ),
                "approved_at": None,
                "published_at": None,
                "updated_at": func.now(),
            },
        )
        .returning(ResearchReport)
    )
    report = session.scalars(statement).one()
    session.commit()

    return report
