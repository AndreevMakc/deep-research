import hashlib
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.agents.verifier import verify_claim
from app.agents.writer import (
    build_writer_packet,
    finalize_report,
    persist_report_artifacts,
    render_report_markdown,
    validate_writer_draft,
)
from app.db.models import (
    Claim,
    ClaimStatus,
    ResearchReport,
    Verification,
    VerificationVerdict,
)
from app.db.repositories import (
    create_or_update_claim,
    create_research_run,
    create_research_tasks,
)
from app.db.session import SessionFactory
from app.schemas.source_document import SourceDocument
from app.schemas.verification import VerificationResult
from app.schemas.writer import (
    CitedStatement,
    ReportSection,
    WriterDraft,
)
from app.source_store import persist_source_document


def main() -> None:
    content = (
        "Verification smoke source\n"
        "The documented workflow verified 42 claims."
    )
    quote = "The documented workflow verified 42 claims."
    document = SourceDocument(
        requested_url="https://example.com/verification-smoke",
        url="https://example.com/verification-smoke",
        canonical_url=(
            "https://example.com/verification-smoke"
        ),
        title="Verification smoke source",
        content=content,
        content_hash=hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
        mime_type="text/plain",
    )
    run_id = None

    def fake_generate(_packet) -> VerificationResult:
        return VerificationResult(
            verdict=VerificationVerdict.SUPPORTED,
            confidence=0.98,
            reason=(
                "The exact source context directly supports "
                "the claim and its numeric value."
            ),
        )

    with tempfile.TemporaryDirectory() as directory:
        with SessionFactory() as session:
            run = create_research_run(
                session=session,
                question="Can a claim be verified?",
            )
            run_id = run.id
            task = create_research_tasks(
                session=session,
                run_id=run.id,
                tasks=[
                    {
                        "title": "Verification",
                        "question": (
                            "Does the source support the claim?"
                        ),
                        "objective": "Verify one persisted claim.",
                        "source_types": ["test"],
                        "search_queries": ["verification"],
                        "priority": 1,
                    }
                ],
            )[0]
            snapshot = persist_source_document(
                session=session,
                run_id=run.id,
                document=document,
                runs_directory=Path(directory),
            )
            claim = create_or_update_claim(
                session=session,
                run_id=run.id,
                research_task_id=task.id,
                source_snapshot_id=snapshot.id,
                text=(
                    "The documented workflow verified "
                    "42 claims."
                ),
                evidence_quote=quote,
                quote_start=content.index(quote),
                quote_end=(
                    content.index(quote) + len(quote)
                ),
                locator={"description": "Smoke evidence"},
                scope="Documented workflow",
                created_by_agent="researcher-v1",
            )
            claim_id = claim.id

        result = verify_claim(
            claim_id,
            generate_fn=fake_generate,
        )
        repeated_result = verify_claim(
            claim_id,
            generate_fn=fake_generate,
        )
        packet = build_writer_packet(run_id)
        statement = CitedStatement(
            text=(
                "The documented workflow verified "
                "42 claims."
            ),
            claim_ids=[str(claim_id)],
        )
        draft = WriterDraft(
            short_answer=[statement],
            sections=[
                ReportSection(
                    heading="Verified result",
                    statements=[statement],
                )
            ],
        )
        validate_writer_draft(draft, packet)
        report = finalize_report(draft, packet)
        markdown = render_report_markdown(report)
        persist_report_artifacts(
            run_id=run_id,
            report=report,
            markdown=markdown,
            runs_directory=Path(directory),
        )
        persist_report_artifacts(
            run_id=run_id,
            report=report,
            markdown=markdown,
            runs_directory=Path(directory),
        )

        with SessionFactory() as session:
            persisted_claim = session.get(Claim, claim_id)
            verifications = list(
                session.scalars(
                    select(Verification).where(
                        Verification.claim_id == claim_id
                    )
                ).all()
            )
            verification = verifications[0]
            reports = list(
                session.scalars(
                    select(ResearchReport).where(
                        ResearchReport.run_id == run_id
                    )
                ).all()
            )

            assert persisted_claim is not None
            assert len(verifications) == 1
            assert (
                result.verdict
                == VerificationVerdict.SUPPORTED
            )
            assert (
                repeated_result.verdict
                == VerificationVerdict.SUPPORTED
            )
            assert (
                persisted_claim.status
                == ClaimStatus.SUPPORTED
            )
            assert verification.confidence == 0.98
            assert verification.checked_source_ids == [
                str(snapshot.source_id)
            ]
            assert len(reports) == 1
            assert reports[0].result_json["sources"][0][
                "source_snapshot_id"
            ] == str(snapshot.id)
            assert (
                Path(directory)
                / str(run_id)
                / "report.md"
            ).is_file()
            assert (
                Path(directory)
                / str(run_id)
                / "report.json"
            ).is_file()

            session.delete(persisted_claim.run)
            session.commit()

    assert run_id is not None
    print("Verification integration smoke test OK")


if __name__ == "__main__":
    main()
