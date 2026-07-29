from __future__ import annotations

import base64
import io
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from app.research_inputs import (
    prepare_material,
    remove_material_file,
    store_material_file,
)


class ResearchInputValidationTests(unittest.TestCase):
    def test_accepts_supported_text_and_pdf_files(self) -> None:
        markdown = prepare_material(
            kind="file",
            filename="brief.md",
            mime_type="text/markdown",
            content_base64=base64.b64encode(
                "# Заголовок".encode()
            ).decode(),
            max_file_bytes=10_000,
            max_text_bytes=10_000,
        )
        self.assertEqual(markdown.kind, "markdown")
        self.assertEqual(markdown.text_content, "# Заголовок")

        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(output)
        pdf = prepare_material(
            kind="file",
            filename="source.pdf",
            mime_type="application/pdf",
            content_base64=base64.b64encode(
                output.getvalue()
            ).decode(),
            max_file_bytes=10_000,
            max_text_bytes=10_000,
        )
        self.assertEqual(pdf.kind, "pdf")
        self.assertEqual(pdf.mime_type, "application/pdf")

    def test_rejects_unsafe_or_invalid_files(self) -> None:
        cases = [
            {
                "filename": "../brief.txt",
                "content": b"safe text",
            },
            {
                "filename": "brief.exe",
                "content": b"binary",
            },
            {
                "filename": "brief.pdf",
                "content": b"not a pdf",
            },
            {
                "filename": "brief.txt",
                "content": b"\xff",
            },
        ]

        for case in cases:
            with self.subTest(filename=case["filename"]):
                with self.assertRaises(ValueError):
                    prepare_material(
                        kind="file",
                        filename=case["filename"],
                        content_base64=base64.b64encode(
                            case["content"]
                        ).decode(),
                        max_file_bytes=10_000,
                        max_text_bytes=10_000,
                    )

    def test_stores_and_removes_only_scoped_material(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tenant_id = uuid.uuid4()
            draft_id = uuid.uuid4()
            material_id = uuid.uuid4()

            with patch(
                "app.research_inputs.INPUT_MATERIALS_DIRECTORY",
                root,
            ):
                stored = store_material_file(
                    tenant_id=tenant_id,
                    draft_id=draft_id,
                    material_id=material_id,
                    extension=".txt",
                    content=b"source",
                )
                path = Path(stored)
                self.assertTrue(path.is_file())
                remove_material_file(stored)
                self.assertFalse(path.exists())

                outside = root.parent / "outside.txt"
                outside.write_text("keep", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    remove_material_file(str(outside))
                self.assertTrue(outside.exists())
                outside.unlink()


if __name__ == "__main__":
    unittest.main()
