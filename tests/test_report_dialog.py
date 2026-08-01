from __future__ import annotations

import unittest
import uuid
import warnings
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import app
from app.config import get_settings
from app.db.models import (
    ApiRole,
    ResearchReport,
    ResearchReportVersion,
    ResearchRun,
    ResearchTask,
    RunStatus,
    TaskStatus,
    Tenant,
    WorkItem,
    WorkStatus,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_password_identity,
    create_tenant,
)
from app.report_dialog import (
    ReportAnswerDraft,
    finalize_follow_up_version,
    validate_report_answer,
)


class ReportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient`.*",
        )

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.slug = f"dialog-{suffix}"
        self.password = "Researcher password 123!"
        self.claim_id = str(uuid.uuid4())
        self.original_result = {
            "direct_answer": {
                "text": "Сохранённый источник подтверждает вывод.",
                "claim_ids": [self.claim_id],
            },
            "key_findings": [],
            "short_answer": [],
            "sections": [],
            "limitations": [],
            "contradictions": [],
            "unanswered_questions": [],
            "sources": [
                {
                    "citation_label": "[1]",
                    "claim_id": self.claim_id,
                    "source_snapshot_id": str(uuid.uuid4()),
                    "source_title": "Сохранённый источник",
                    "source_url": "https://example.com/source",
                    "evidence_quote": "Источник подтверждает вывод.",
                    "verdict": "supported",
                }
            ],
        }
        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Report dialog test",
            )
            admin = create_password_identity(
                session,
                tenant=tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password="Admin password 123!",
            )
            researcher = create_password_identity(
                session,
                tenant=tenant,
                subject="researcher",
                role=ApiRole.RESEARCHER,
                password=self.password,
                actor=admin,
            )
            run = ResearchRun(
                tenant_id=tenant.id,
                created_by_identity_id=researcher.id,
                question="Что подтверждено?",
                title="Проверенный отчёт",
                status=RunStatus.COMPLETED,
            )
            session.add(run)
            session.flush()
            session.add_all(
                [
                    ResearchTask(
                        run_id=run.id,
                        task_type="web_research",
                        question=run.question,
                        status=TaskStatus.COMPLETED,
                        priority=1,
                        input_data={},
                        output_data={},
                    ),
                    ResearchReport(
                        run_id=run.id,
                        markdown_path="report.md",
                        json_path="report.json",
                        markdown_hash="a" * 64,
                        json_hash="b" * 64,
                        result_json=self.original_result,
                    ),
                    WorkItem(
                        tenant_id=tenant.id,
                        run_id=run.id,
                        kind="execute_research_run",
                        status=WorkStatus.SUCCEEDED,
                        payload={"run_id": str(run.id)},
                    ),
                ]
            )
            session.commit()
            self.tenant_id = tenant.id
            self.run_id = run.id

        self.client = TestClient(app, base_url="https://testserver")
        response = self.client.post(
            "/api/v1/auth/login",
            json={
                "tenant": self.slug,
                "login": "researcher",
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
            session.commit()

    def _csrf_headers(self, key: str | None = None) -> dict[str, str]:
        headers = {
            "X-CSRF-Token": self.client.cookies.get(get_settings().csrf_cookie_name)
        }
        if key:
            headers["Idempotency-Key"] = key
        return headers

    def test_grounded_answer_keeps_report_references(self) -> None:
        draft = ReportAnswerDraft(
            answer="Вывод подтверждён сохранённым источником.",
            claim_ids=[self.claim_id],
            section_ids=["report-answer"],
        )
        answer = validate_report_answer(
            draft,
            self.original_result,
        )
        with patch(
            "app.api.answer_report_question",
            return_value=answer,
        ):
            response = self.client.post(
                f"/api/v1/runs/{self.run_id}/questions",
                headers=self._csrf_headers(),
                json={"question": "Чем подтверждён вывод?"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["sections"][0]["id"],
            "report-answer",
        )
        self.assertEqual(
            response.json()["citations"][0]["claim_id"],
            self.claim_id,
        )

    def test_search_is_not_started_without_confirmation(self) -> None:
        answer = {
            "answer": None,
            "needs_research": True,
            "missing_information": "Нужны более свежие данные.",
            "sections": [],
            "citations": [],
        }
        with patch(
            "app.api.answer_report_question",
            return_value=answer,
        ):
            response = self.client.post(
                f"/api/v1/runs/{self.run_id}/questions",
                headers=self._csrf_headers(),
                json={"question": "Что изменилось сегодня?"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["needs_research"])
        with SessionFactory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchTask.id)).where(
                        ResearchTask.run_id == self.run_id,
                        ResearchTask.task_type == "report_follow_up_research",
                    )
                ),
                0,
            )

    def test_confirmed_search_creates_one_new_version(self) -> None:
        question = "Что изменилось сегодня?"
        body = {
            "question": question,
            "reason": "В сохранённом отчёте нет свежих данных.",
        }
        headers = self._csrf_headers("report-follow-up-0001")
        first = self.client.post(
            f"/api/v1/runs/{self.run_id}/follow-ups",
            headers=headers,
            json=body,
        )
        replay = self.client.post(
            f"/api/v1/runs/{self.run_id}/follow-ups",
            headers=headers,
            json=body,
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(first.json()["task_id"], replay.json()["task_id"])

        task_id = uuid.UUID(first.json()["task_id"])
        updated = {
            **self.original_result,
            "direct_answer": {
                "text": "Дополнительный источник обновил вывод.",
                "claim_ids": [self.claim_id],
            },
            "sources": [
                *self.original_result["sources"],
                {
                    "claim_id": str(uuid.uuid4()),
                    "source_snapshot_id": str(uuid.uuid4()),
                },
            ],
        }
        with SessionFactory() as session:
            task = session.get(ResearchTask, task_id)
            report = session.scalar(
                select(ResearchReport).where(ResearchReport.run_id == self.run_id)
            )
            task.status = TaskStatus.COMPLETED
            task.output_data = {"summary": "Найдены свежие данные"}
            report.result_json = updated
            report.markdown_hash = "c" * 64
            report.json_hash = "d" * 64
            session.commit()

        self.assertEqual(finalize_follow_up_version(task_id), 2)
        self.assertEqual(finalize_follow_up_version(task_id), 2)
        with SessionFactory() as session:
            versions = list(
                session.scalars(
                    select(ResearchReportVersion)
                    .where(ResearchReportVersion.run_id == self.run_id)
                    .order_by(ResearchReportVersion.version_number)
                ).all()
            )
            self.assertEqual([version.version_number for version in versions], [1, 2])
            self.assertEqual(versions[1].reason, question)
            self.assertEqual(versions[1].requested_by, "researcher")

        run = self.client.get(f"/api/v1/runs/{self.run_id}")
        previous = self.client.get(f"/api/v1/runs/{self.run_id}/versions/1")
        current = self.client.get(f"/api/v1/runs/{self.run_id}/versions/2")
        self.assertEqual(run.status_code, 200)
        self.assertEqual(len(run.json()["versions"]), 2)
        self.assertTrue(run.json()["versions"][1]["current"])
        self.assertEqual(previous.json()["result"], self.original_result)
        self.assertEqual(current.json()["result"], updated)


if __name__ == "__main__":
    unittest.main()
