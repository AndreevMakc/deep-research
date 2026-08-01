from __future__ import annotations

import io
import json
import uuid
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.writer import render_report_markdown
from app.db.models import SourceSnapshot
from app.schemas.writer import FinalResearchReport
from app.source_store import read_source_snapshot_content


def render_markdown(result: dict) -> str:
    return render_report_markdown(
        FinalResearchReport.model_validate(result)
    )


def build_report_package(
    session: Session,
    *,
    run_id: uuid.UUID,
    result: dict,
) -> bytes:
    snapshot_ids = {
        uuid.UUID(source["source_snapshot_id"])
        for source in result.get("sources", [])
        if source.get("source_snapshot_id")
    }
    snapshots = list(
        session.scalars(
            select(SourceSnapshot).where(
                SourceSnapshot.run_id == run_id,
                SourceSnapshot.id.in_(snapshot_ids),
            ).order_by(SourceSnapshot.retrieved_at, SourceSnapshot.id)
        ).all()
    )
    manifest = {
        "run_id": str(run_id),
        "sources": [
            {
                "id": str(snapshot.id),
                "file": f"sources/{snapshot.content_hash}.txt",
                "url": snapshot.final_url,
                "content_hash": snapshot.content_hash,
                "mime_type": snapshot.mime_type,
                "retrieved_at": snapshot.retrieved_at.isoformat(),
            }
            for snapshot in snapshots
        ],
    }
    # ponytail: in-memory ZIP is bounded by run source limits; stream if
    # package sizes become a measured worker-memory problem.
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("report.md", render_markdown(result))
        archive.writestr(
            "report.json",
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        archive.writestr(
            "manifest.json",
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        written_hashes = set()
        for snapshot in snapshots:
            if snapshot.content_hash in written_hashes:
                continue
            archive.writestr(
                f"sources/{snapshot.content_hash}.txt",
                read_source_snapshot_content(snapshot),
            )
            written_hashes.add(snapshot.content_hash)
    return output.getvalue()
