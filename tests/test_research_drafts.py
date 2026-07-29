from __future__ import annotations

import unittest
import uuid
import warnings
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import app
from app.config import get_settings
from app.db.models import (
    ApiRole,
    ResearchDraft,
    ResearchDraftStatus,
    ResearchRun,
    ReviewerIdentity,
    Tenant,
    WorkItem,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_password_identity,
    create_tenant,
)
from app.research_drafts import (
    build_clarification_questions,
    interpret_research_question,
    refine_interpretation_with_answers,
)


class ResearchDraftInterpretationTests(unittest.TestCase):
    def test_builds_public_confirmation_fields(self) -> None:
        interpretation = interpret_research_question(
            "  Какие   решения существуют? ",
            max_run_seconds=3_600,
        )

        self.assertIn(
            "Какие решения существуют?",
            interpretation.scope,
        )
        self.assertTrue(interpretation.period)
        self.assertEqual(
            interpretation.estimated_duration_minutes,
            60,
        )
        self.assertEqual(len(interpretation.assumptions), 2)

    def test_distinguishes_specific_and_ambiguous_questions(
        self,
    ) -> None:
        specific = build_clarification_questions(
            "Сравнить PostgreSQL и MySQL для российских "
            "B2B-команд за 2025 год по стоимости "
            "владения и безопасности."
        )
        ambiguous = build_clarification_questions(
            "Какая платформа лучше?"
        )

        self.assertEqual(specific, [])
        self.assertGreaterEqual(len(ambiguous), 2)
        self.assertLessEqual(len(ambiguous), 4)
        self.assertTrue(
            all(question.options for question in ambiguous)
        )

    def test_answers_refine_public_interpretation(
        self,
    ) -> None:
        interpretation = interpret_research_question(
            "Какая платформа лучше?",
            max_run_seconds=600,
        )
        refined = refine_interpretation_with_answers(
            interpretation,
            questions=[
                {
                    "id": "scope",
                    "prompt": "Какой охват?",
                    "options": [],
                },
                {
                    "id": "period",
                    "prompt": "Какой период?",
                    "options": [],
                },
            ],
            answers=[
                {
                    "question_id": "scope",
                    "answer": "Сравнить три варианта",
                    "skipped": False,
                },
                {
                    "question_id": "period",
                    "answer": "Последние 12 месяцев",
                    "skipped": False,
                },
            ],
        )

        self.assertIn("Сравнить три варианта", refined.scope)
        self.assertEqual(
            refined.period,
            "Последние 12 месяцев",
        )
        self.assertGreater(
            len(refined.assumptions),
            len(interpretation.assumptions),
        )


class ResearchDraftApiTests(unittest.TestCase):
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
        suffix = uuid.uuid4().hex[:10]
        self.slug = f"draft-{suffix}"
        self.other_slug = f"draft-other-{suffix}"
        self.password = "Researcher password 123!"

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Research draft test",
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
            other_researcher = create_password_identity(
                session,
                tenant=tenant,
                subject="other-researcher",
                role=ApiRole.RESEARCHER,
                password="Other researcher password 123!",
                actor=admin,
            )
            other_tenant = create_tenant(
                session,
                slug=self.other_slug,
                name="Other research draft test",
            )
            other_admin = create_password_identity(
                session,
                tenant=other_tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password="Other admin password 123!",
            )
            self.tenant_id = tenant.id
            self.researcher_id = researcher.id
            self.other_researcher_id = other_researcher.id
            self.other_tenant_id = other_tenant.id
            self.other_admin_id = other_admin.id

        self.client = TestClient(
            app,
            base_url="https://testserver",
        )
        self.other_client = TestClient(
            app,
            base_url="https://testserver",
        )
        self.cross_tenant_client = TestClient(
            app,
            base_url="https://testserver",
        )
        self.assertEqual(
            self._login(
                self.client,
                tenant=self.slug,
                login="researcher",
                password=self.password,
            ),
            200,
        )
        self.assertEqual(
            self._login(
                self.other_client,
                tenant=self.slug,
                login="other-researcher",
                password="Other researcher password 123!",
            ),
            200,
        )
        self.assertEqual(
            self._login(
                self.cross_tenant_client,
                tenant=self.other_slug,
                login="admin",
                password="Other admin password 123!",
            ),
            200,
        )

    def tearDown(self) -> None:
        self.client.close()
        self.other_client.close()
        self.cross_tenant_client.close()

        with SessionFactory() as session:
            for tenant_id in (
                self.tenant_id,
                self.other_tenant_id,
            ):
                tenant = session.get(Tenant, tenant_id)

                if tenant is not None:
                    session.delete(tenant)

            reviewers = list(
                session.scalars(
                    select(ReviewerIdentity).where(
                        ReviewerIdentity.subject.like(
                            f"{self.slug}:%"
                        )
                        | ReviewerIdentity.subject.like(
                            f"{self.other_slug}:%"
                        )
                    )
                ).all()
            )

            for reviewer in reviewers:
                session.delete(reviewer)

            session.commit()

    @staticmethod
    def _login(
        client: TestClient,
        *,
        tenant: str,
        login: str,
        password: str,
    ) -> int:
        return client.post(
            "/api/v1/auth/login",
            json={
                "tenant": tenant,
                "login": login,
                "password": password,
            },
        ).status_code

    @staticmethod
    def _headers(
        client: TestClient,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        headers = {
            "X-CSRF-Token": client.cookies.get(
                get_settings().csrf_cookie_name
            )
        }

        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        return headers

    def _create_draft(
        self,
        question: str = (
            "Сравнить PostgreSQL и MySQL для российских "
            "B2B-команд за 2025 год по стоимости "
            "владения и безопасности."
        ),
    ) -> dict:
        response = self.client.post(
            "/api/v1/research-drafts",
            headers=self._headers(
                self.client,
                idempotency_key="create-draft-0001",
            ),
            json={"question": question},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_draft_is_persisted_updated_and_confirmed_once(
        self,
    ) -> None:
        created = self._create_draft()
        self.assertEqual(created["status"], "draft")
        self.assertEqual(created["revision"], 1)
        self.assertFalse(
            created["clarification"]["required"]
        )
        self.assertTrue(
            created["clarification"]["completed"]
        )
        self.assertIsNone(created["run_id"])
        self.assertTrue(created["scope"])
        self.assertTrue(created["period"])
        self.assertEqual(len(created["assumptions"]), 2)
        self.assertNotIn("agent_plan", created)

        replay = self.client.post(
            "/api/v1/research-drafts",
            headers=self._headers(
                self.client,
                idempotency_key="create-draft-0001",
            ),
            json={"question": created["question"]},
        )
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(
            replay.headers["Idempotency-Replayed"],
            "true",
        )
        self.assertEqual(replay.json()["id"], created["id"])

        current = self.client.get(
            "/api/v1/research-drafts/current"
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["id"], created["id"])

        updated = self.client.patch(
            f"/api/v1/research-drafts/{created['id']}",
            headers=self._headers(self.client),
            json={
                "revision": created["revision"],
                "scope": "Сравнить доступные альтернативы",
                "period": "Данные за последние 12 месяцев",
                "assumptions": [
                    "Рассматриваются зрелые решения",
                    "Цены сравниваются без скидок",
                ],
            },
        )
        self.assertEqual(updated.status_code, 200)
        updated_payload = updated.json()
        self.assertEqual(
            updated_payload["scope"],
            "Сравнить доступные альтернативы",
        )
        self.assertEqual(updated_payload["revision"], 2)

        edited_again = self.client.patch(
            f"/api/v1/research-drafts/{created['id']}",
            headers=self._headers(self.client),
            json={
                "revision": updated_payload["revision"],
                "scope": "Сравнить три доступные альтернативы",
                "period": updated_payload["period"],
                "assumptions": updated_payload["assumptions"],
            },
        )
        self.assertEqual(edited_again.status_code, 200)
        latest = edited_again.json()
        self.assertEqual(latest["revision"], 3)

        stale_update = self.client.patch(
            f"/api/v1/research-drafts/{created['id']}",
            headers=self._headers(self.client),
            json={
                "revision": created["revision"],
                "scope": "Устаревшее изменение охвата",
                "period": "Другой период",
                "assumptions": ["Не должно сохраниться"],
            },
        )
        self.assertEqual(stale_update.status_code, 409)
        conflict = stale_update.json()["detail"]
        self.assertEqual(
            conflict["code"],
            "draft_revision_conflict",
        )
        self.assertEqual(
            conflict["current_draft"]["revision"],
            latest["revision"],
        )

        current_after_edits = self.client.get(
            "/api/v1/research-drafts/current"
        )
        self.assertEqual(current_after_edits.status_code, 200)
        self.assertEqual(
            current_after_edits.json()["scope"],
            latest["scope"],
        )
        self.assertEqual(
            current_after_edits.json()["revision"],
            latest["revision"],
        )

        with SessionFactory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchDraft.id)).where(
                        ResearchDraft.tenant_id
                        == self.tenant_id
                    )
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchRun.id)).where(
                        ResearchRun.tenant_id
                        == self.tenant_id
                    )
                ),
                0,
            )

        stale_confirmation = self.client.post(
            (
                f"/api/v1/research-drafts/"
                f"{created['id']}/confirm"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="confirm-stale-draft-0001",
            ),
            json={"revision": updated_payload["revision"]},
        )
        self.assertEqual(stale_confirmation.status_code, 409)

        confirmed = self.client.post(
            (
                f"/api/v1/research-drafts/"
                f"{created['id']}/confirm"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="confirm-draft-0001",
            ),
            json={"revision": latest["revision"]},
        )
        self.assertEqual(confirmed.status_code, 202)
        self.assertEqual(
            confirmed.json()["draft_revision"],
            latest["revision"],
        )
        run_id = confirmed.json()["run_id"]

        confirmed_replay = self.client.post(
            (
                f"/api/v1/research-drafts/"
                f"{created['id']}/confirm"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="confirm-draft-0001",
            ),
            json={"revision": latest["revision"]},
        )
        self.assertEqual(confirmed_replay.status_code, 202)
        self.assertEqual(
            confirmed_replay.headers[
                "Idempotency-Replayed"
            ],
            "true",
        )
        self.assertEqual(
            confirmed_replay.json()["run_id"],
            run_id,
        )

        duplicate = self.client.post(
            (
                f"/api/v1/research-drafts/"
                f"{created['id']}/confirm"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="confirm-draft-0002",
            ),
            json={"revision": latest["revision"]},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIsNone(
            self.client.get(
                "/api/v1/research-drafts/current"
            ).json()
        )

        with SessionFactory() as session:
            draft = session.get(
                ResearchDraft,
                uuid.UUID(created["id"]),
            )
            self.assertIsNotNone(draft)
            self.assertEqual(
                draft.status,
                ResearchDraftStatus.CONFIRMED,
            )
            self.assertEqual(
                draft.revision,
                latest["revision"],
            )
            self.assertEqual(
                draft.scope,
                latest["scope"],
            )
            self.assertEqual(str(draft.run_id), run_id)
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchRun.id)).where(
                        ResearchRun.tenant_id
                        == self.tenant_id
                    )
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count(WorkItem.id)).where(
                        WorkItem.tenant_id == self.tenant_id
                    )
                ),
                1,
            )

    def test_clarification_dialog_is_finite_and_idempotent(
        self,
    ) -> None:
        created = self._create_draft(
            "Какая платформа лучше?"
        )
        clarification = created["clarification"]
        self.assertTrue(clarification["required"])
        self.assertFalse(clarification["completed"])
        self.assertGreaterEqual(
            clarification["total_steps"],
            2,
        )
        self.assertLessEqual(
            clarification["total_steps"],
            4,
        )
        self.assertEqual(clarification["current_step"], 1)

        blocked_confirmation = self.client.post(
            (
                f"/api/v1/research-drafts/"
                f"{created['id']}/confirm"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="confirm-before-answers-0001",
            ),
            json={"revision": created["revision"]},
        )
        self.assertEqual(
            blocked_confirmation.status_code,
            409,
        )
        self.assertEqual(
            blocked_confirmation.json()["detail"]["code"],
            "clarification_required",
        )

        first_question = clarification["current_question"]
        first_payload = {
            "revision": created["revision"],
            "question_id": first_question["id"],
            "answer": first_question["options"][0],
            "skipped": False,
        }
        first_answer = self.client.post(
            (
                f"/api/v1/research-drafts/{created['id']}"
                "/clarifications/answer"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="answer-clarification-0001",
            ),
            json=first_payload,
        )
        self.assertEqual(first_answer.status_code, 200)
        current = first_answer.json()
        self.assertEqual(current["revision"], 2)
        self.assertEqual(
            current["clarification"]["current_step"],
            2,
        )

        replay = self.client.post(
            (
                f"/api/v1/research-drafts/{created['id']}"
                "/clarifications/answer"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="answer-clarification-0001",
            ),
            json=first_payload,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            replay.headers["Idempotency-Replayed"],
            "true",
        )
        self.assertEqual(
            replay.json()["clarification"]["current_step"],
            2,
        )

        recovered = self.client.get(
            "/api/v1/research-drafts/current"
        )
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(
            recovered.json()["clarification"]["current_step"],
            2,
        )

        answer_index = 2

        while not current["clarification"]["completed"]:
            question = current["clarification"][
                "current_question"
            ]
            skipped = answer_index == 2
            response = self.client.post(
                (
                    f"/api/v1/research-drafts/"
                    f"{created['id']}/clarifications/answer"
                ),
                headers=self._headers(
                    self.client,
                    idempotency_key=(
                        "answer-clarification-"
                        f"{answer_index:04d}"
                    ),
                ),
                json={
                    "revision": current["revision"],
                    "question_id": question["id"],
                    "answer": (
                        None
                        if skipped
                        else question["options"][0]
                    ),
                    "skipped": skipped,
                },
            )
            self.assertEqual(response.status_code, 200)
            current = response.json()
            answer_index += 1

        self.assertEqual(
            len(current["clarification"]["answers"]),
            current["clarification"]["total_steps"],
        )
        self.assertEqual(
            current["revision"],
            1 + current["clarification"]["total_steps"],
        )
        self.assertTrue(current["scope"])
        self.assertGreater(len(current["assumptions"]), 2)

        stale_repeat = self.client.post(
            (
                f"/api/v1/research-drafts/{created['id']}"
                "/clarifications/answer"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="answer-stale-clarification-0001",
            ),
            json=first_payload,
        )
        self.assertEqual(stale_repeat.status_code, 409)

        confirmed = self.client.post(
            (
                f"/api/v1/research-drafts/"
                f"{created['id']}/confirm"
            ),
            headers=self._headers(
                self.client,
                idempotency_key="confirm-after-answers-0001",
            ),
            json={"revision": current["revision"]},
        )
        self.assertEqual(confirmed.status_code, 202)

        with SessionFactory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchRun.id)).where(
                        ResearchRun.tenant_id
                        == self.tenant_id
                    )
                ),
                1,
            )

    def test_draft_is_hidden_from_other_identities(
        self,
    ) -> None:
        created = self._create_draft()
        path = f"/api/v1/research-drafts/{created['id']}"

        self.assertEqual(
            self.other_client.get(path).status_code,
            404,
        )
        self.assertEqual(
            self.cross_tenant_client.get(path).status_code,
            404,
        )
        denied = self.other_client.post(
            f"{path}/confirm",
            headers=self._headers(
                self.other_client,
                idempotency_key="confirm-hidden-draft-0001",
            ),
            json={"revision": created["revision"]},
        )
        self.assertEqual(denied.status_code, 404)
        denied_update = self.cross_tenant_client.patch(
            path,
            headers=self._headers(
                self.cross_tenant_client,
            ),
            json={
                "revision": created["revision"],
                "scope": "Недоступное изменение",
                "period": "Любой период",
                "assumptions": ["Не должно сохраниться"],
            },
        )
        self.assertEqual(denied_update.status_code, 404)

        ambiguous = self.client.post(
            "/api/v1/research-drafts",
            headers=self._headers(
                self.client,
                idempotency_key="create-hidden-clarification",
            ),
            json={"question": "Какой рынок выбрать?"},
        ).json()
        question = ambiguous["clarification"][
            "current_question"
        ]
        denied_answer = self.cross_tenant_client.post(
            (
                f"/api/v1/research-drafts/"
                f"{ambiguous['id']}/clarifications/answer"
            ),
            headers=self._headers(
                self.cross_tenant_client,
                idempotency_key="answer-hidden-clarification",
            ),
            json={
                "revision": ambiguous["revision"],
                "question_id": question["id"],
                "answer": question["options"][0],
                "skipped": False,
            },
        )
        self.assertEqual(denied_answer.status_code, 404)


class ResearchDraftDashboardTests(unittest.TestCase):
    def test_dashboard_uses_confirmation_flow(self) -> None:
        dashboard = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "dashboard.html"
        ).read_text(encoding="utf-8")

        self.assertIn('id="draft-card"', dashboard)
        self.assertIn("Изменить детали", dashboard)
        self.assertIn('id="draft-edit-form"', dashboard)
        self.assertIn('id="clarification-card"', dashboard)
        self.assertIn("draft_revision_conflict", dashboard)
        self.assertIn(
            '"/api/v1/research-drafts"',
            dashboard,
        )
        self.assertIn(
            "/api/v1/research-drafts/${activeDraft.id}/confirm",
            dashboard,
        )


if __name__ == "__main__":
    unittest.main()
