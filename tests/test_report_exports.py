from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import uuid
import warnings
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import app
from app.db.models import (
    ApiRole,
    ResearchReport,
    ResearchRun,
    ReviewerIdentity,
    RunStatus,
    Source,
    SourceSnapshot,
    Tenant,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_password_identity,
    create_tenant,
)


class ReportExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient`.*",
        )

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.slug = f"exports-{suffix}"
        self.password = "Export password 123!"
        self.directory = tempfile.TemporaryDirectory()
        content = "Проверенный immutable snapshot."
        snapshot_path = Path(self.directory.name) / "source.txt"
        snapshot_path.write_text(content, encoding="utf-8")
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Report export test",
            )
            admin = create_password_identity(
                session,
                tenant=tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password=self.password,
            )
            create_password_identity(
                session,
                tenant=tenant,
                subject="viewer",
                role=ApiRole.VIEWER,
                password=self.password,
                actor=admin,
            )
            run = ResearchRun(
                tenant_id=tenant.id,
                created_by_identity_id=admin.id,
                question="Что подтверждено?",
                title="Экспортируемый отчёт",
                status=RunStatus.COMPLETED,
            )
            session.add(run)
            session.flush()
            source = Source(
                run_id=run.id,
                url="https://example.com/source",
                canonical_url="https://example.com/source",
                title="Example source",
            )
            session.add(source)
            session.flush()
            snapshot = SourceSnapshot(
                source_id=source.id,
                run_id=run.id,
                final_url=source.canonical_url,
                content_hash=content_hash,
                mime_type="text/plain",
                local_path=str(snapshot_path),
                http_status=200,
                content_length=len(content.encode()),
                metadata_json={},
            )
            session.add(snapshot)
            session.flush()
            statement = {
                "text": "Подтверждён проверяемый результат.",
                "claim_ids": [str(uuid.uuid4())],
                "qualification": None,
            }
            result = {
                "run_id": str(run.id),
                "question": run.question,
                "direct_answer": statement,
                "key_findings": [],
                "short_answer": [],
                "sections": [],
                "limitations": [],
                "contradictions": [],
                "unanswered_questions": [],
                "sources": [
                    {
                        "citation_label": "C1",
                        "claim_id": statement["claim_ids"][0],
                        "source_snapshot_id": str(snapshot.id),
                        "source_url": source.canonical_url,
                        "source_title": source.title,
                        "source_publisher": None,
                        "source_published_at": None,
                        "source_retrieved_at": None,
                        "evidence_quote": content,
                        "verdict": "supported",
                        "confidence": 0.9,
                        "verification_reason": "Exact snapshot evidence.",
                    }
                ],
                "overall_confidence": 0.9,
                "quality_summary": {
                    "confirmed_claims": 1,
                    "limited_claims": 0,
                    "contradicted_claims": 0,
                    "unsupported_claims": 0,
                    "source_count": 1,
                    "overall_confidence": 0.9,
                    "caveats": [],
                },
            }
            session.add(
                ResearchReport(
                    run_id=run.id,
                    markdown_path="unused.md",
                    json_path="unused.json",
                    markdown_hash="a" * 64,
                    json_hash="b" * 64,
                    result_json=result,
                )
            )
            session.commit()
            self.tenant_id = tenant.id
            self.run_id = run.id
            self.content = content
            self.content_hash = content_hash

        self.admin = self._client("admin")
        self.viewer = self._client("viewer")

    def _client(self, login: str) -> TestClient:
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            "/api/v1/auth/login",
            json={
                "tenant": self.slug,
                "login": login,
                "password": self.password,
            },
        )
        self.assertEqual(response.status_code, 200)
        return client

    def tearDown(self) -> None:
        self.admin.close()
        self.viewer.close()
        self.directory.cleanup()
        with SessionFactory() as session:
            tenant = session.get(Tenant, self.tenant_id)
            if tenant is not None:
                session.delete(tenant)
            for reviewer in session.scalars(
                select(ReviewerIdentity).where(
                    ReviewerIdentity.subject.like(f"{self.slug}:%")
                )
            ):
                session.delete(reviewer)
            session.commit()

    def test_markdown_and_analyst_package_exports(self) -> None:
        markdown = self.viewer.get(
            f"/api/v1/runs/{self.run_id}/export/markdown?version=1"
        )
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("text/markdown", markdown.headers["content-type"])
        self.assertIn("attachment", markdown.headers["content-disposition"])
        self.assertIn("Подтверждён проверяемый результат.", markdown.text)
        self.assertIn("https://example.com/source", markdown.text)

        denied = self.viewer.get(
            f"/api/v1/runs/{self.run_id}/export/package"
        )
        self.assertEqual(denied.status_code, 403)
        package = self.admin.get(
            f"/api/v1/runs/{self.run_id}/export/package"
        )
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "report.md",
                    "report.json",
                    "manifest.json",
                    f"sources/{self.content_hash}.txt",
                },
            )
            self.assertEqual(
                archive.read(
                    f"sources/{self.content_hash}.txt"
                ).decode(),
                self.content,
            )
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["run_id"], str(self.run_id))


if __name__ == "__main__":
    unittest.main()
