import hashlib
import json
import tempfile
from pathlib import Path

from app.db.models import (
    Claim,
    ClaimReviewStatus,
    ReportReviewStatus,
    ResearchReport,
    ResearchRun,
    ResearchTask,
    ReviewDecisionType,
    ReviewerIdentity,
    ReviewerRole,
    Source,
    SourceSnapshot,
    TaskStatus,
    Verification,
    VerificationVerdict,
)
from app.db.repositories import (
    create_research_run,
    get_research_report,
    get_review_decisions_for_run,
    record_claim_review,
    record_report_review,
)
from app.db.session import SessionFactory
from app.operations import export_to_obsidian, publish_report


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        report_directory = root / "run"
        report_directory.mkdir()
        markdown = b"# Verified report\n"
        json_content = b'{"status":"verified"}\n'
        markdown_path = report_directory / "report.md"
        json_path = report_directory / "report.json"
        markdown_path.write_bytes(markdown)
        json_path.write_bytes(json_content)

        with SessionFactory() as session:
            run = create_research_run(
                session,
                "Human review smoke",
                limits={
                    "max_external_requests": 5,
                    "max_sources": 5,
                    "max_claims": 5,
                    "max_tokens": 1_000,
                    "max_run_seconds": 60,
                },
            )
            run_id = run.id
            reviewer = ReviewerIdentity(
                subject="smoke",
                display_name="Smoke Reviewer",
                role=ReviewerRole.ADMIN,
                active=True,
            )
            task = ResearchTask(
                run_id=run_id,
                task_type="web_research",
                question="Evidence?",
                status=TaskStatus.COMPLETED,
                priority=1,
                input_data={},
                output_data={},
            )
            source = Source(
                run_id=run_id,
                url="https://example.com/review",
                canonical_url=(
                    "https://example.com/review"
                ),
                title="Review source",
            )
            session.add_all([reviewer, task, source])
            session.flush()
            reviewer_id = reviewer.id
            snapshot = SourceSnapshot(
                source_id=source.id,
                run_id=run_id,
                final_url=source.canonical_url,
                content_hash="a" * 64,
                mime_type="text/plain",
                local_path=str(root / "source.txt"),
                content_length=5,
                metadata_json={},
            )
            session.add(snapshot)
            session.flush()
            claim = Claim(
                run_id=run_id,
                research_task_id=task.id,
                source_snapshot_id=snapshot.id,
                text="Approved fact",
                evidence_quote="fact",
                quote_start=0,
                quote_end=4,
                locator={},
                created_by_agent="smoke",
            )
            session.add(claim)
            session.flush()
            verification = Verification(
                claim_id=claim.id,
                verifier_agent="verifier-v1",
                verdict=VerificationVerdict.SUPPORTED,
                confidence=1.0,
                reason="Exact evidence",
                checked_source_ids=[str(source.id)],
            )
            report = ResearchReport(
                run_id=run_id,
                markdown_path=str(markdown_path),
                json_path=str(json_path),
                markdown_hash=_sha256(markdown),
                json_hash=_sha256(json_content),
                result_json={
                    "sources": [
                        {
                            "claim_id": str(claim.id),
                        }
                    ]
                },
            )
            session.add_all([verification, report])
            session.commit()
            claim_id = claim.id

            try:
                record_report_review(
                    session,
                    run_id,
                    decision=ReviewDecisionType.APPROVE,
                    reason="Too early",
                    reviewer="smoke",
                    reviewer_identity_id=reviewer.id,
                )
            except RuntimeError:
                session.rollback()
            else:
                raise AssertionError(
                    "Unreviewed claim passed report gate"
                )

            record_claim_review(
                session,
                claim_id,
                decision=ReviewDecisionType.APPROVE,
                reason="Evidence checked",
                reviewer="smoke",
                reviewer_identity_id=reviewer.id,
            )
            record_report_review(
                session,
                run_id,
                decision=ReviewDecisionType.APPROVE,
                reason="All claims reviewed",
                reviewer="smoke",
                reviewer_identity_id=reviewer.id,
            )

        published_markdown, published_json = publish_report(
            run_id,
            reason="Ready",
            reviewer="smoke",
        )
        assert published_markdown.exists()
        assert published_json.exists()

        note_path, export_json_path = export_to_obsidian(
            run_id,
            root / "vault",
            reviewer="smoke",
        )
        assert "Approved fact" in note_path.read_text(
            encoding="utf-8"
        )
        exported = json.loads(
            export_json_path.read_text(encoding="utf-8")
        )
        assert len(exported["claims"]) == 1
        assert (
            exported["claims"][0]["review_status"]
            == "approved"
        )

        with SessionFactory() as session:
            report = get_research_report(session, run_id)
            assert report is not None
            assert (
                report.review_status
                == ReportReviewStatus.PUBLISHED
            )
            claim = session.get(Claim, claim_id)
            assert claim is not None
            assert (
                claim.review_status
                == ClaimReviewStatus.APPROVED
            )
            decisions = get_review_decisions_for_run(
                session,
                run_id,
            )
            assert [
                entry.decision
                for entry in decisions
            ] == [
                ReviewDecisionType.APPROVE,
                ReviewDecisionType.APPROVE,
                ReviewDecisionType.PUBLISH,
            ]
            run = session.get(ResearchRun, run_id)
            assert run is not None
            session.delete(run)
            reviewer = session.get(
                ReviewerIdentity,
                reviewer_id,
            )

            if reviewer is not None:
                session.delete(reviewer)

            session.commit()

    print("Human review and publication gate smoke test OK")


if __name__ == "__main__":
    main()
