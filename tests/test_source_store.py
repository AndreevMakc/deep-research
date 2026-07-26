import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path

from app.db.models import SourceSnapshot
from app.source_store import (
    read_source_snapshot_content,
)


class SourceStoreTests(unittest.TestCase):
    def test_rejects_snapshot_hash_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_text(
                "Stored source content",
                encoding="utf-8",
            )
            snapshot = SourceSnapshot(
                id=uuid.uuid4(),
                source_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                final_url="https://example.com/source",
                content_hash="0" * 64,
                mime_type="text/plain",
                local_path=str(path),
                content_length=len(
                    "Stored source content".encode(
                        "utf-8"
                    )
                ),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "hash mismatch",
            ):
                read_source_snapshot_content(
                    snapshot
                )

    def test_rejects_snapshot_length_mismatch(
        self,
    ) -> None:
        content = "Stored source content"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_text(
                content,
                encoding="utf-8",
            )
            snapshot = SourceSnapshot(
                id=uuid.uuid4(),
                source_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                final_url="https://example.com/source",
                content_hash=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                mime_type="text/plain",
                local_path=str(path),
                content_length=1,
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "length mismatch",
            ):
                read_source_snapshot_content(
                    snapshot
                )


if __name__ == "__main__":
    unittest.main()
