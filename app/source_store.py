from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Claim, SourceSnapshot
from app.db.repositories import (
    create_source_snapshot,
    upsert_source,
)
from app.schemas.source_document import SourceDocument


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIRECTORY = PROJECT_ROOT / "data" / "runs"


def save_source_content(
    run_id: uuid.UUID,
    document: SourceDocument,
    *,
    runs_directory: Path = RUNS_DIRECTORY,
) -> Path:
    source_directory = (
        runs_directory
        / str(run_id)
        / "sources"
    )
    source_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        source_directory
        / f"{document.content_hash}.txt"
    )

    if path.exists():
        stored_content = path.read_text(
            encoding="utf-8"
        )

        if stored_content != document.content:
            raise RuntimeError(
                "Stored source content does not match "
                f"its hash path: {path}"
            )
    else:
        path.write_text(
            document.content,
            encoding="utf-8",
        )

    return path


def persist_source_document(
    session: Session,
    run_id: uuid.UUID,
    document: SourceDocument,
    *,
    search_title: str | None = None,
    search_query: str | None = None,
    runs_directory: Path = RUNS_DIRECTORY,
) -> SourceSnapshot:
    path = save_source_content(
        run_id=run_id,
        document=document,
        runs_directory=runs_directory,
    )

    try:
        local_path = str(
            path.relative_to(PROJECT_ROOT)
        )
    except ValueError:
        local_path = str(path)

    metadata = {
        **document.metadata_json,
        "requested_url": document.requested_url,
    }

    if search_query:
        metadata["search_query"] = search_query

    source = upsert_source(
        session=session,
        run_id=run_id,
        url=document.url,
        canonical_url=document.canonical_url,
        title=document.title or search_title,
    )

    return create_source_snapshot(
        session=session,
        run_id=run_id,
        source_id=source.id,
        final_url=document.url,
        content_hash=document.content_hash,
        mime_type=document.mime_type,
        local_path=local_path,
        http_status=document.http_status,
        content_length=len(
            document.content.encode("utf-8")
        ),
        metadata_json=metadata,
    )


def read_source_snapshot_content(
    snapshot: SourceSnapshot,
) -> str:
    if not snapshot.local_path:
        raise RuntimeError(
            "Source snapshot has no local content: "
            f"{snapshot.id}"
        )

    path = Path(snapshot.local_path)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    content = path.read_text(encoding="utf-8")
    content_bytes = content.encode("utf-8")
    actual_hash = hashlib.sha256(
        content_bytes
    ).hexdigest()

    if actual_hash != snapshot.content_hash:
        raise RuntimeError(
            "Source snapshot content hash mismatch: "
            f"{snapshot.id}"
        )

    if (
        snapshot.content_length is not None
        and len(content_bytes) != snapshot.content_length
    ):
        raise RuntimeError(
            "Source snapshot content length mismatch: "
            f"{snapshot.id}"
        )

    return content


def read_claim_evidence(claim: Claim) -> str:
    if claim.source_snapshot is None:
        raise RuntimeError(
            f"Claim has no source snapshot: {claim.id}"
        )

    if (
        claim.quote_start is None
        or claim.quote_end is None
    ):
        raise RuntimeError(
            f"Claim has no quote coordinates: {claim.id}"
        )

    content = read_source_snapshot_content(
        claim.source_snapshot
    )
    evidence = content[
        claim.quote_start:claim.quote_end
    ]

    if evidence != claim.evidence_quote:
        raise RuntimeError(
            "Claim evidence does not match its source "
            f"snapshot: {claim.id}"
        )

    return evidence
