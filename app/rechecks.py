from __future__ import annotations

import argparse
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.agents.verifier import (
    VERIFIER_AGENT,
    verify_claim,
)
from app.agents.writer import writer_node
from app.db.models import (
    Claim,
    ClaimRecheckRequest,
    ClaimRecheckStatus,
    SourceSnapshot,
    Verification,
    VerificationVerdict,
)
from app.db.repositories import (
    create_verification,
    get_claim,
    get_research_report,
    snapshot_research_report_version,
)
from app.db.session import SessionFactory
from app.schemas.source_document import SourceDocument
from app.schemas.verification import VerificationResult
from app.source_store import (
    RUNS_DIRECTORY,
    persist_source_document,
)
from app.tools.source_fetch import (
    SourceFetchError,
    fetch_source,
)


FetchSource = Callable[[str], SourceDocument]
VerifyClaim = Callable[[uuid.UUID], VerificationResult]
RegenerateReport = Callable[[uuid.UUID], None]


def recheck_payload(
    request: ClaimRecheckRequest,
) -> dict:
    def timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "id": str(request.id),
        "run_id": str(request.run_id),
        "claim_id": str(request.claim_id),
        "category": request.category.value,
        "comment": request.comment,
        "status": request.status.value,
        "requested_by": request.requested_by,
        "original_snapshot_id": (
            str(request.original_snapshot_id)
            if request.original_snapshot_id
            else None
        ),
        "result_snapshot_id": (
            str(request.result_snapshot_id)
            if request.result_snapshot_id
            else None
        ),
        "original_verdict": (
            request.original_verdict.value
            if request.original_verdict
            else None
        ),
        "result_verdict": (
            request.result_verdict.value
            if request.result_verdict
            else None
        ),
        "material_changed": request.material_changed,
        "report_version_number": (
            request.report_version_number
        ),
        "details": request.details_json,
        "error": request.error,
        "created_at": timestamp(request.created_at),
        "started_at": timestamp(request.started_at),
        "completed_at": timestamp(request.completed_at),
    }


def _regenerate_report(run_id: uuid.UUID) -> None:
    writer_node({"run_id": str(run_id)})


def _mark_retryable_failure(
    recheck_id: uuid.UUID,
    error: Exception,
) -> None:
    with SessionFactory() as session:
        request = session.get(
            ClaimRecheckRequest,
            recheck_id,
        )

        if request is None:
            return

        request.status = ClaimRecheckStatus.QUEUED
        request.error = (
            f"{type(error).__name__}: {error}"
        )
        session.commit()


def _dependency_claim_ids(
    claim: Claim,
) -> list[uuid.UUID]:
    snapshot = claim.source_snapshot

    if snapshot is None:
        return [claim.id]

    with SessionFactory() as session:
        return list(
            session.scalars(
                select(Claim.id)
                .join(
                    SourceSnapshot,
                    SourceSnapshot.id
                    == Claim.source_snapshot_id,
                )
                .where(
                    Claim.run_id == claim.run_id,
                    SourceSnapshot.source_id
                    == snapshot.source_id,
                )
                .order_by(Claim.created_at, Claim.id)
            ).all()
        )


def execute_claim_recheck(
    recheck_id: uuid.UUID,
    *,
    fetch_fn: FetchSource = fetch_source,
    verify_fn: VerifyClaim = verify_claim,
    regenerate_fn: RegenerateReport = _regenerate_report,
    runs_directory: Path = RUNS_DIRECTORY,
) -> dict:
    try:
        with SessionFactory() as session:
            request = session.get(
                ClaimRecheckRequest,
                recheck_id,
            )

            if request is None:
                raise RuntimeError(
                    f"Claim recheck not found: {recheck_id}"
                )

            if (
                request.status
                == ClaimRecheckStatus.COMPLETED
            ):
                return recheck_payload(request)

            request.status = ClaimRecheckStatus.RUNNING
            request.started_at = (
                request.started_at
                or datetime.now(timezone.utc)
            )
            request.error = None
            session.commit()
            claim_id = request.claim_id
            run_id = request.run_id
            requested_by = request.requested_by
            reason = (
                f"Перепроверка claim {claim_id}: "
                f"{request.category.value}"
            )

        with SessionFactory() as session:
            claim = get_claim(session, claim_id)

            if claim is None:
                raise RuntimeError(
                    f"Claim not found: {claim_id}"
                )

            dependency_ids = _dependency_claim_ids(claim)
            snapshot = claim.source_snapshot
            source_url = (
                snapshot.source.canonical_url
                if (
                    snapshot is not None
                    and snapshot.source is not None
                )
                else (
                    snapshot.final_url
                    if snapshot is not None
                    else None
                )
            )
            original_snapshot_by_claim = {
                item.id: item.source_snapshot_id
                for item in session.scalars(
                    select(Claim).where(
                        Claim.id.in_(dependency_ids)
                    )
                ).all()
            }
            verifications = session.scalars(
                select(Verification).where(
                    Verification.claim_id.in_(
                        dependency_ids
                    ),
                    Verification.verifier_agent
                    == VERIFIER_AGENT,
                )
            ).all()
            original_verdict_by_claim = {
                item.claim_id: item.verdict
                for item in verifications
            }
            original_report = get_research_report(
                session,
                run_id,
            )
            original_report_hash = (
                original_report.json_hash
                if original_report is not None
                else None
            )

        document: SourceDocument | None = None
        fetch_error: SourceFetchError | None = None

        if source_url is None:
            fetch_error = SourceFetchError(
                "Claim source URL is unavailable"
            )
        else:
            try:
                document = fetch_fn(source_url)
            except SourceFetchError as error:
                fetch_error = error

        result_snapshot_id: uuid.UUID | None = None

        if document is not None:
            with SessionFactory() as session:
                snapshot = persist_source_document(
                    session=session,
                    run_id=run_id,
                    document=document,
                    runs_directory=runs_directory,
                )
                result_snapshot_id = snapshot.id
                dependencies = session.scalars(
                    select(Claim).where(
                        Claim.id.in_(dependency_ids)
                    )
                ).all()

                for dependency in dependencies:
                    quote = dependency.evidence_quote or ""
                    start = (
                        document.content.find(quote)
                        if quote
                        else -1
                    )
                    dependency.source_snapshot_id = snapshot.id
                    dependency.quote_start = (
                        start if start >= 0 else None
                    )
                    dependency.quote_end = (
                        start + len(quote)
                        if start >= 0
                        else None
                    )

                session.commit()

            results = {
                dependency_id: verify_fn(dependency_id)
                for dependency_id in dependency_ids
            }
        else:
            unavailable = VerificationResult(
                verdict=(
                    VerificationVerdict.SOURCE_UNAVAILABLE
                ),
                confidence=1.0,
                reason=(
                    "Повторная загрузка источника не удалась: "
                    f"{fetch_error}"
                ),
            )
            results = {}

            for dependency_id in dependency_ids:
                with SessionFactory() as session:
                    create_verification(
                        session=session,
                        claim_id=dependency_id,
                        verifier_agent=VERIFIER_AGENT,
                        verdict=unavailable.verdict,
                        confidence=unavailable.confidence,
                        reason=unavailable.reason,
                        checked_source_ids=[],
                    )

                results[dependency_id] = unavailable

        evidence_changed = any(
            (
                original_verdict_by_claim.get(
                    dependency_id
                )
                != result.verdict
                or (
                    result_snapshot_id is not None
                    and original_snapshot_by_claim.get(
                        dependency_id
                    )
                    != result_snapshot_id
                )
            )
            for dependency_id, result in results.items()
        )
        report_version_number: int | None = None
        material_changed = False

        if evidence_changed and original_report is not None:
            with SessionFactory() as session:
                current_report = get_research_report(
                    session,
                    run_id,
                )
                if current_report is None:
                    raise RuntimeError(
                        "Research report disappeared"
                    )
                snapshot_research_report_version(
                    session,
                    report=current_report,
                    reason="Исходная версия отчёта",
                    requested_by=None,
                )
                session.commit()
            regenerate_fn(run_id)

            with SessionFactory() as session:
                updated_report = get_research_report(
                    session,
                    run_id,
                )

                if updated_report is None:
                    raise RuntimeError(
                        "Writer did not persist a report"
                    )

                material_changed = (
                    updated_report.json_hash
                    != original_report_hash
                )

                if material_changed:
                    version = snapshot_research_report_version(
                        session,
                        report=updated_report,
                        reason=reason,
                        requested_by=requested_by,
                        claim_recheck_id=recheck_id,
                    )
                    session.commit()
                    report_version_number = (
                        version.version_number
                    )

        selected_result = results[claim_id]
        details = {
            "affected_claims": [
                {
                    "claim_id": str(dependency_id),
                    "original_snapshot_id": (
                        str(
                            original_snapshot_by_claim[
                                dependency_id
                            ]
                        )
                        if original_snapshot_by_claim.get(
                            dependency_id
                        )
                        else None
                    ),
                    "result_snapshot_id": (
                        str(result_snapshot_id)
                        if result_snapshot_id
                        else None
                    ),
                    "original_verdict": (
                        original_verdict_by_claim[
                            dependency_id
                        ].value
                        if original_verdict_by_claim.get(
                            dependency_id
                        )
                        else None
                    ),
                    "result_verdict": result.verdict.value,
                    "reason": result.reason,
                }
                for dependency_id, result in results.items()
            ],
            "evidence_changed": evidence_changed,
            "source_fetch_error": (
                str(fetch_error)
                if fetch_error is not None
                else None
            ),
        }

        with SessionFactory() as session:
            request = session.get(
                ClaimRecheckRequest,
                recheck_id,
            )

            if request is None:
                raise RuntimeError(
                    f"Claim recheck not found: {recheck_id}"
                )

            request.status = ClaimRecheckStatus.COMPLETED
            request.result_snapshot_id = result_snapshot_id
            request.result_verdict = selected_result.verdict
            request.material_changed = material_changed
            request.report_version_number = (
                report_version_number
            )
            request.details_json = details
            request.error = None
            request.completed_at = datetime.now(
                timezone.utc
            )
            session.commit()
            session.refresh(request)
            return recheck_payload(request)
    except Exception as error:
        _mark_retryable_failure(recheck_id, error)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recheck one claim and its source dependencies."
    )
    parser.add_argument("recheck_id", type=uuid.UUID)
    arguments = parser.parse_args(argv)
    execute_claim_recheck(arguments.recheck_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
