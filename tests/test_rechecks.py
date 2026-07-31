from __future__ import annotations

import hashlib
import tempfile
import unittest
import uuid
import warnings
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agents.verifier import VERIFIER_AGENT
from app.api import app
from app.config import get_settings
from app.db.models import (
    ApiRole,
    Claim,
    ClaimRecheckCategory,
    ClaimRecheckRequest,
    ClaimRecheckStatus,
    ResearchReport,
    ResearchReportVersion,
    ResearchRun,
    ResearchTask,
    ReviewerIdentity,
    RunStatus,
    TaskStatus,
    Tenant,
    VerificationVerdict,
    WorkItem,
    WorkStatus,
)
from app.db.repositories import (
    create_or_update_claim,
    create_verification,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_password_identity,
    create_tenant,
)
from app.queue import finish_work
from app.rechecks import execute_claim_recheck
from app.schemas.source_document import SourceDocument
from app.schemas.verification import VerificationResult
from app.source_store import persist_source_document
from app.tools.source_fetch import SourceFetchError


SOURCE_URL = "https://example.com/recheck-source"
SOURCE_CONTENT = (
    "Evidence\n"
    "The source confirms 42 verified records."
)
EVIDENCE_QUOTE = "The source confirms 42 verified records."


class ClaimRecheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warnings.filterwarnings(
            "ignore",
            message=(
                "Using `httpx` with "
                "`starlette.testclient`.*"
            ),
        )

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        suffix = uuid.uuid4().hex[:10]
        self.slug = f"recheck-{suffix}"
        self.password = "Viewer password 123!"
        self.document = SourceDocument(
            requested_url=SOURCE_URL,
            url=SOURCE_URL,
            canonical_url=SOURCE_URL,
            title="Recheck source",
            content=SOURCE_CONTENT,
            content_hash=hashlib.sha256(
                SOURCE_CONTENT.encode("utf-8")
            ).hexdigest(),
            mime_type="text/plain",
            http_status=200,
        )

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Claim recheck test",
            )
            admin = create_password_identity(
                session,
                tenant=tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password="Admin password 123!",
            )
            viewer = create_password_identity(
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
                question="How many records were verified?",
                title="Verified records",
                status=RunStatus.COMPLETED,
            )
            session.add(run)
            session.flush()
            task = ResearchTask(
                run_id=run.id,
                task_type="web_research",
                question=run.question,
                status=TaskStatus.COMPLETED,
                priority=1,
                input_data={},
                output_data={},
            )
            session.add(task)
            session.flush()
            snapshot = persist_source_document(
                session=session,
                run_id=run.id,
                document=self.document,
                runs_directory=Path(self.directory.name),
            )
            claim = create_or_update_claim(
                session=session,
                run_id=run.id,
                research_task_id=task.id,
                source_snapshot_id=snapshot.id,
                text="The source confirms 42 records.",
                evidence_quote=EVIDENCE_QUOTE,
                quote_start=SOURCE_CONTENT.index(
                    EVIDENCE_QUOTE
                ),
                quote_end=len(SOURCE_CONTENT),
                locator={"section": "Evidence"},
                scope="Stored source",
                created_by_agent="researcher-v1",
            )
            create_verification(
                session=session,
                claim_id=claim.id,
                verifier_agent=VERIFIER_AGENT,
                verdict=VerificationVerdict.SUPPORTED,
                confidence=0.95,
                reason="The quote directly supports the claim.",
                checked_source_ids=[str(snapshot.source_id)],
            )
            session.add(
                ResearchReport(
                    run_id=run.id,
                    markdown_path="report.md",
                    json_path="report.json",
                    markdown_hash="a" * 64,
                    json_hash="b" * 64,
                    result_json={"answer": "42 records"},
                )
            )
            session.commit()
            self.tenant_id = tenant.id
            self.viewer_id = viewer.id
            self.run_id = run.id
            self.claim_id = claim.id
            self.snapshot_id = snapshot.id

        self.client = TestClient(
            app,
            base_url="https://testserver",
        )
        response = self.client.post(
            "/api/v1/auth/login",
            json={
                "tenant": self.slug,
                "login": "viewer",
                "password": self.password,
            },
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self) -> None:
        self.client.close()

        with SessionFactory() as session:
            tenant = session.get(Tenant, self.tenant_id)

            if tenant is not None:
                session.delete(tenant)

            reviewers = list(
                session.scalars(
                    select(ReviewerIdentity).where(
                        ReviewerIdentity.subject.like(
                            f"{self.slug}:%"
                        )
                    )
                ).all()
            )

            for reviewer in reviewers:
                session.delete(reviewer)

            session.commit()

        self.directory.cleanup()

    def _new_request(self) -> uuid.UUID:
        with SessionFactory() as session:
            request = ClaimRecheckRequest(
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                claim_id=self.claim_id,
                requested_by_identity_id=self.viewer_id,
                requested_by="viewer",
                category=(
                    ClaimRecheckCategory.SOURCE_OUTDATED
                ),
                comment="Check the latest source.",
                original_snapshot_id=self.snapshot_id,
                original_verdict=(
                    VerificationVerdict.SUPPORTED
                ),
            )
            session.add(request)
            session.commit()
            return request.id

    @staticmethod
    def _supported_verification(
        claim_id: uuid.UUID,
    ) -> VerificationResult:
        result = VerificationResult(
            verdict=VerificationVerdict.SUPPORTED,
            confidence=0.95,
            reason="The quote directly supports the claim.",
        )

        with SessionFactory() as session:
            create_verification(
                session=session,
                claim_id=claim_id,
                verifier_agent=VERIFIER_AGENT,
                verdict=result.verdict,
                confidence=result.confidence,
                reason=result.reason,
                checked_source_ids=[],
            )

        return result

    def test_same_evidence_does_not_create_report_version(
        self,
    ) -> None:
        with SessionFactory() as session:
            dependency = Claim(
                run_id=self.run_id,
                source_snapshot_id=self.snapshot_id,
                text=(
                    "The same source confirms verified records."
                ),
                evidence_quote=EVIDENCE_QUOTE,
                quote_start=SOURCE_CONTENT.index(
                    EVIDENCE_QUOTE
                ),
                quote_end=len(SOURCE_CONTENT),
                locator={"section": "Evidence"},
                scope="Stored source dependency",
                created_by_agent="researcher-v1",
            )
            session.add(dependency)
            session.commit()
            create_verification(
                session=session,
                claim_id=dependency.id,
                verifier_agent=VERIFIER_AGENT,
                verdict=VerificationVerdict.SUPPORTED,
                confidence=0.9,
                reason="The same source supports the claim.",
                checked_source_ids=[],
            )

        recheck_id = self._new_request()
        regenerated: list[uuid.UUID] = []
        result = execute_claim_recheck(
            recheck_id,
            fetch_fn=lambda _url: self.document,
            verify_fn=self._supported_verification,
            regenerate_fn=regenerated.append,
            runs_directory=Path(self.directory.name),
        )

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["material_changed"])
        self.assertEqual(
            result["result_verdict"],
            "supported",
        )
        self.assertEqual(regenerated, [])
        self.assertEqual(
            len(result["details"]["affected_claims"]),
            2,
        )

        with SessionFactory() as session:
            self.assertEqual(
                session.scalar(
                    select(
                        func.count(
                            ResearchReportVersion.id
                        )
                    ).where(
                        ResearchReportVersion.run_id
                        == self.run_id
                    )
                ),
                0,
            )

    def test_unavailable_source_updates_report_once(
        self,
    ) -> None:
        recheck_id = self._new_request()

        def unavailable(_url: str) -> SourceDocument:
            raise SourceFetchError("HTTP 503")

        def regenerate(run_id: uuid.UUID) -> None:
            with SessionFactory() as session:
                report = session.scalar(
                    select(ResearchReport).where(
                        ResearchReport.run_id == run_id
                    )
                )
                self.assertIsNotNone(report)
                report.result_json = {
                    "answer": "Source is unavailable"
                }
                report.markdown_hash = "c" * 64
                report.json_hash = "d" * 64
                session.commit()

        result = execute_claim_recheck(
            recheck_id,
            fetch_fn=unavailable,
            regenerate_fn=regenerate,
            runs_directory=Path(self.directory.name),
        )

        self.assertEqual(
            result["result_verdict"],
            "source_unavailable",
        )
        self.assertTrue(result["material_changed"])
        self.assertEqual(
            result["report_version_number"],
            2,
        )
        self.assertIn(
            "HTTP 503",
            result["details"]["source_fetch_error"],
        )

        with SessionFactory() as session:
            versions = list(
                session.scalars(
                    select(ResearchReportVersion)
                    .where(
                        ResearchReportVersion.run_id
                        == self.run_id
                    )
                    .order_by(
                        ResearchReportVersion.version_number
                    )
                ).all()
            )
            self.assertEqual(
                [
                    version.version_number
                    for version in versions
                ],
                [1, 2],
            )
            self.assertEqual(
                versions[1].claim_recheck_id,
                recheck_id,
            )

    def test_viewer_request_is_idempotent_and_tenant_scoped(
        self,
    ) -> None:
        headers = {
            "X-CSRF-Token": self.client.cookies.get(
                get_settings().csrf_cookie_name
            ),
            "Idempotency-Key": "claim-recheck-0001",
        }
        body = {
            "category": "evidence_incorrect",
            "comment": "The quote may not support the conclusion.",
        }
        first = self.client.post(
            f"/api/v1/claims/{self.claim_id}/rechecks",
            headers=headers,
            json=body,
        )
        replay = self.client.post(
            f"/api/v1/claims/{self.claim_id}/rechecks",
            headers=headers,
            json=body,
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(first.json()["id"], replay.json()["id"])
        self.assertEqual(
            replay.headers["Idempotency-Replayed"],
            "true",
        )
        conflict = self.client.post(
            f"/api/v1/claims/{self.claim_id}/rechecks",
            headers={
                **headers,
                "Idempotency-Key": "claim-recheck-0002",
            },
            json={
                **body,
                "comment": "A second concurrent request.",
            },
        )
        self.assertEqual(conflict.status_code, 409)
        history = self.client.get(
            f"/api/v1/claims/{self.claim_id}/rechecks"
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)
        self.assertEqual(
            history.json()[0]["requested_by"],
            "viewer",
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/runs/{self.run_id}/provenance"
            ).status_code,
            403,
        )
        admin_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            logged_in = admin_client.post(
                "/api/v1/auth/login",
                json={
                    "tenant": self.slug,
                    "login": "admin",
                    "password": "Admin password 123!",
                },
            )
            self.assertEqual(logged_in.status_code, 200)
            provenance = admin_client.get(
                f"/api/v1/runs/{self.run_id}/provenance"
            )
            self.assertEqual(provenance.status_code, 200)
            claim = provenance.json()["claims"][0]
            self.assertEqual(
                claim["source_snapshot"]["id"],
                str(self.snapshot_id),
            )
            self.assertEqual(
                claim["locator"],
                {"section": "Evidence"},
            )
            self.assertEqual(
                claim["verification"]["verdict"],
                "supported",
            )
            self.assertEqual(len(claim["rechecks"]), 1)
        finally:
            admin_client.close()

        with SessionFactory() as session:
            other = create_tenant(
                session,
                slug=f"other-{uuid.uuid4().hex[:8]}",
                name="Other tenant",
            )
            other_admin = create_password_identity(
                session,
                tenant=other,
                subject="admin",
                role=ApiRole.ADMIN,
                password="Other admin password 123!",
            )
            other_identity = create_password_identity(
                session,
                tenant=other,
                subject="viewer",
                role=ApiRole.VIEWER,
                password="Other viewer password 123!",
                actor=other_admin,
            )
            other_slug = other.slug
            other_id = other.id
            self.assertIsNotNone(other_identity.id)

        other_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            other_client.post(
                "/api/v1/auth/login",
                json={
                    "tenant": other_slug,
                    "login": "viewer",
                    "password": "Other viewer password 123!",
                },
            )
            denied = other_client.post(
                (
                    f"/api/v1/claims/{self.claim_id}"
                    "/rechecks"
                ),
                headers={
                    "X-CSRF-Token": (
                        other_client.cookies.get(
                            get_settings().csrf_cookie_name
                        )
                    ),
                    "Idempotency-Key": (
                        "cross-tenant-recheck-0001"
                    ),
                },
                json=body,
            )
            self.assertEqual(denied.status_code, 404)
        finally:
            other_client.close()

            with SessionFactory() as session:
                other = session.get(Tenant, other_id)

                if other is not None:
                    session.delete(other)

                session.commit()

    def test_terminal_recheck_failure_does_not_fail_run(
        self,
    ) -> None:
        recheck_id = self._new_request()

        with SessionFactory() as session:
            item = WorkItem(
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                kind="recheck_claim",
                status=WorkStatus.LEASED,
                payload={
                    "recheck_id": str(recheck_id),
                    "claim_id": str(self.claim_id),
                },
                lease_owner="worker",
                attempts=3,
                max_attempts=3,
            )
            session.add(item)
            session.commit()
            item_id = item.id

        with SessionFactory() as session:
            finish_work(
                session,
                item_id=item_id,
                worker_id="worker",
                succeeded=False,
                error="Source fetch crashed",
            )

        with SessionFactory() as session:
            run = session.get(ResearchRun, self.run_id)
            request = session.get(
                ClaimRecheckRequest,
                recheck_id,
            )
            self.assertEqual(
                run.status,
                RunStatus.COMPLETED,
            )
            self.assertEqual(
                request.status,
                ClaimRecheckStatus.FAILED,
            )

    def test_dashboard_exposes_recheck_without_manual_verdicts(
        self,
    ) -> None:
        dashboard = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "dashboard.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Проверить этот вывод", dashboard)
        self.assertIn("Технический verdict", dashboard)
        self.assertNotIn(
            'for (const decision of ["approve", "reject", "research"])',
            dashboard,
        )


if __name__ == "__main__":
    unittest.main()
