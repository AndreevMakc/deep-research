from __future__ import annotations

import unittest
import uuid
import warnings

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import app
from app.config import get_settings
from app.db.models import (
    ApiRole,
    ResearchRun,
    ResearchTask,
    ReviewerIdentity,
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
from app.queue import finish_work


class NotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient`.*",
        )

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.slug = f"notifications-{suffix}"
        self.password = "Notification password 123!"
        self.worker_id = "notification-worker"

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Notification test",
            )
            owner = create_password_identity(
                session,
                tenant=tenant,
                subject="owner",
                role=ApiRole.ADMIN,
                password=self.password,
            )
            create_password_identity(
                session,
                tenant=tenant,
                subject="reader",
                role=ApiRole.VIEWER,
                password=self.password,
                actor=owner,
            )
            run = ResearchRun(
                tenant_id=tenant.id,
                created_by_identity_id=owner.id,
                question="Can a partial result be retried?",
                title="Partial result",
                status=RunStatus.COMPLETED_WITH_ERRORS,
            )
            session.add(run)
            session.flush()
            session.add_all(
                [
                    ResearchTask(
                        run_id=run.id,
                        task_type="research",
                        question="Completed direction",
                        status=TaskStatus.COMPLETED,
                        priority=1,
                        input_data={},
                        output_data={"summary": "Saved"},
                    ),
                    ResearchTask(
                        run_id=run.id,
                        task_type="research",
                        question="Failed direction",
                        status=TaskStatus.FAILED,
                        priority=2,
                        input_data={},
                        output_data={"error": {"message": "Retry me"}},
                    ),
                ]
            )
            item = WorkItem(
                tenant_id=tenant.id,
                run_id=run.id,
                kind="execute_research_run",
                status=WorkStatus.LEASED,
                payload={"run_id": str(run.id)},
                lease_owner=self.worker_id,
                attempts=1,
                max_attempts=3,
                cancel_requested=False,
                pause_requested=False,
                finish_requested=False,
            )
            session.add(item)
            session.commit()
            finish_work(
                session,
                item_id=item.id,
                worker_id=self.worker_id,
                succeeded=True,
            )
            self.tenant_id = tenant.id
            self.run_id = run.id

        self.owner = TestClient(app, base_url="https://testserver")
        self.reader = TestClient(app, base_url="https://testserver")
        for client, login in (
            (self.owner, "owner"),
            (self.reader, "reader"),
        ):
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "tenant": self.slug,
                    "login": login,
                    "password": self.password,
                },
            )
            self.assertEqual(response.status_code, 200)

    def tearDown(self) -> None:
        self.owner.close()
        self.reader.close()
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

    def _headers(self, client: TestClient, key: str) -> dict:
        return {
            "X-CSRF-Token": client.cookies.get(
                get_settings().csrf_cookie_name
            ),
            "Idempotency-Key": key,
        }

    def test_notifications_are_persistent_per_user_and_retryable(
        self,
    ) -> None:
        owner_notifications = self.owner.get(
            "/api/v1/notifications"
        ).json()
        reader_notifications = self.reader.get(
            "/api/v1/notifications"
        ).json()
        self.assertEqual(owner_notifications["unread_count"], 1)
        self.assertEqual(reader_notifications["unread_count"], 1)
        notification_id = owner_notifications["items"][0]["id"]

        marked = self.owner.post(
            f"/api/v1/notifications/{notification_id}/read",
            headers=self._headers(self.owner, "read-notification-0001"),
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(
            self.owner.get("/api/v1/notifications").json()[
                "unread_count"
            ],
            0,
        )
        self.assertEqual(
            self.reader.get("/api/v1/notifications").json()[
                "unread_count"
            ],
            1,
        )

        progress = self.owner.get(
            f"/api/v1/runs/{self.run_id}/progress"
        ).json()
        self.assertTrue(progress["actions"]["can_resume"])
        self.assertEqual(
            progress["partial_results"],
            [
                {
                    "title": "Completed direction",
                    "summary": "Saved",
                }
            ],
        )
        self.assertEqual(
            progress["limitations"],
            ["Не завершено направлений: 1."],
        )

        resumed = self.owner.post(
            f"/api/v1/runs/{self.run_id}/resume",
            headers=self._headers(self.owner, "retry-partial-run-0001"),
        )
        self.assertEqual(resumed.status_code, 202)
        self.assertEqual(resumed.json()["work_status"], "queued")
        with SessionFactory() as session:
            tasks = list(
                session.scalars(
                    select(ResearchTask)
                    .where(ResearchTask.run_id == self.run_id)
                    .order_by(ResearchTask.priority)
                ).all()
            )
            self.assertEqual(
                [task.status for task in tasks],
                [TaskStatus.COMPLETED, TaskStatus.FAILED],
            )


if __name__ == "__main__":
    unittest.main()
