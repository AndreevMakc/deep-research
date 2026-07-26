import hashlib
import tempfile
from pathlib import Path

from app.db.repositories import (
    create_or_update_claim,
    create_research_run,
    create_research_tasks,
    create_verification,
)
from app.db.models import (
    ClaimStatus,
    VerificationVerdict,
)
from app.db.session import SessionFactory
from app.schemas.source_document import SourceDocument
from app.source_store import (
    persist_source_document,
    read_claim_evidence,
    read_source_snapshot_content,
)


def main() -> None:
    content = (
        "Stored source heading\n"
        "This exact quote supports the smoke claim."
    )
    document = SourceDocument(
        requested_url="https://example.com/source",
        url="https://example.com/source",
        canonical_url="https://example.com/source",
        title="Persistence smoke source",
        content=content,
        content_hash=hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
        mime_type="text/plain",
    )
    updated_content = (
        "Stored source heading\n"
        "A newer version contains different evidence."
    )
    updated_document = SourceDocument(
        requested_url=document.requested_url,
        url=document.url,
        canonical_url=document.canonical_url,
        title=document.title,
        content=updated_content,
        content_hash=hashlib.sha256(
            updated_content.encode("utf-8")
        ).hexdigest(),
        mime_type=document.mime_type,
    )

    with tempfile.TemporaryDirectory() as directory:
        runs_directory = Path(directory)

        with SessionFactory() as session:
            run = create_research_run(
                session=session,
                question=(
                    "Can sources and claims be persisted?"
                ),
            )
            task = create_research_tasks(
                session=session,
                run_id=run.id,
                tasks=[
                    {
                        "title": "Persistence",
                        "question": (
                            "Can a claim keep task provenance?"
                        ),
                        "objective": (
                            "Verify source snapshot persistence."
                        ),
                        "source_types": ["test"],
                        "search_queries": ["test"],
                        "priority": 1,
                    }
                ],
            )[0]
            snapshot = persist_source_document(
                session=session,
                run_id=run.id,
                document=document,
                search_title=document.title,
                search_query="persistence smoke",
                runs_directory=runs_directory,
            )
            repeated_snapshot = persist_source_document(
                session=session,
                run_id=run.id,
                document=document,
                runs_directory=runs_directory,
            )

            assert snapshot.id == repeated_snapshot.id
            assert read_source_snapshot_content(
                snapshot
            ) == (
                document.content
            )
            updated_snapshot = persist_source_document(
                session=session,
                run_id=run.id,
                document=updated_document,
                runs_directory=runs_directory,
            )

            assert updated_snapshot.id != snapshot.id
            assert (
                updated_snapshot.source_id
                == snapshot.source_id
            )
            assert read_source_snapshot_content(
                snapshot
            ) == document.content
            assert read_source_snapshot_content(
                updated_snapshot
            ) == updated_document.content

            claim = create_or_update_claim(
                session=session,
                run_id=run.id,
                research_task_id=task.id,
                source_snapshot_id=snapshot.id,
                text=(
                    "The source supports persistence."
                ),
                evidence_quote=(
                    "This exact quote supports "
                    "the smoke claim."
                ),
                quote_start=document.content.index(
                    "This exact quote"
                ),
                quote_end=len(document.content),
                locator={
                    "description": "Stored source heading",
                },
                scope="Persistence smoke test",
                created_by_agent="researcher-v1",
            )
            repeated_claim = create_or_update_claim(
                session=session,
                run_id=run.id,
                research_task_id=task.id,
                source_snapshot_id=snapshot.id,
                text=claim.text,
                evidence_quote=claim.evidence_quote,
                quote_start=claim.quote_start,
                quote_end=claim.quote_end,
                locator=claim.locator,
                scope=claim.scope,
                created_by_agent="researcher-v1",
            )

            assert claim.id == repeated_claim.id
            assert (
                claim.source_snapshot_id
                == snapshot.id
            )
            assert claim.research_task_id == task.id
            assert (
                claim.evidence_quote
                == document.content[
                    claim.quote_start:claim.quote_end
                ]
            )
            assert read_claim_evidence(claim) == (
                claim.evidence_quote
            )
            verification = create_verification(
                session=session,
                claim_id=claim.id,
                verifier_agent="verifier-v1",
                verdict=VerificationVerdict.SUPPORTED,
                confidence=0.95,
                reason=(
                    "The immutable source snapshot directly "
                    "supports the smoke claim."
                ),
                checked_source_ids=[
                    str(snapshot.id)
                ],
            )
            session.refresh(claim)

            assert (
                verification.claim_id == claim.id
            )
            assert (
                verification.verdict
                == VerificationVerdict.SUPPORTED
            )
            assert (
                claim.status
                == ClaimStatus.SUPPORTED
            )

            session.delete(run)
            session.commit()

    print("Source and claim persistence smoke test OK")


if __name__ == "__main__":
    main()
