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
from app.queue import (
    finish_work,
    heartbeat_work,
    request_run_early_completion,
    request_run_pause,
    request_run_resume,
)


class RunControlTests(unittest.TestCase):
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
        self.slug = f"control-{suffix}"
        self.password = "Admin password 123!"

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Run control test",
            )
            identity = create_password_identity(
                session,
                tenant=tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password=self.password,
            )
            run = ResearchRun(
                tenant_id=tenant.id,
                created_by_identity_id=identity.id,
                question="Research safely?",
                title="Research safely",
                status=RunStatus.CREATED,
            )
            session.add(run)
            session.flush()
            item = WorkItem(
                tenant_id=tenant.id,
                run_id=run.id,
                kind="execute_research_run",
                status=WorkStatus.QUEUED,
                payload={"run_id": str(run.id)},
                attempts=0,
                max_attempts=3,
                cancel_requested=False,
                pause_requested=False,
                finish_requested=False,
            )
            session.add(item)
            session.commit()
            self.tenant_id = tenant.id
            self.run_id = run.id
            self.item_id = item.id

        self.client = TestClient(
            app,
            base_url="https://testserver",
        )
        logged_in = self.client.post(
            "/api/v1/auth/login",
            json={
                "tenant": self.slug,
                "login": "admin",
                "password": self.password,
            },
        )
        self.assertEqual(logged_in.status_code, 200)

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

    def _headers(self, key: str) -> dict:
        cookie_name = get_settings().csrf_cookie_name
        return {
            "X-CSRF-Token": self.client.cookies.get(
                cookie_name
            ),
            "Idempotency-Key": key,
        }

    def test_pause_and_resume_queued_run_are_idempotent(
        self,
    ) -> None:
        paused = self.client.post(
            f"/api/v1/runs/{self.run_id}/pause",
            headers=self._headers("pause-run-control-0001"),
        )
        self.assertEqual(paused.status_code, 202)
        self.assertEqual(
            paused.json()["run_status"],
            "paused",
        )
        replay = self.client.post(
            f"/api/v1/runs/{self.run_id}/pause",
            headers=self._headers("pause-run-control-0001"),
        )
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(
            replay.headers["Idempotency-Replayed"],
            "true",
        )

        progress = self.client.get(
            f"/api/v1/runs/{self.run_id}/progress",
        )
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.json()["stage"], "paused")
        self.assertTrue(
            progress.json()["actions"]["can_resume"]
        )

        resumed = self.client.post(
            f"/api/v1/runs/{self.run_id}/resume",
            headers=self._headers("resume-run-control-0001"),
        )
        self.assertEqual(resumed.status_code, 202)
        self.assertEqual(
            resumed.json()["work_status"],
            "queued",
        )

    def test_leased_pause_preserves_attempt_budget(
        self,
    ) -> None:
        with SessionFactory() as session:
            item = session.get(WorkItem, self.item_id)
            item.status = WorkStatus.LEASED
            item.lease_owner = "worker-1"
            item.attempts = 1
            run = session.get(ResearchRun, self.run_id)
            run.status = RunStatus.RUNNING
            session.commit()

            request_run_pause(
                session,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
            )
            self.assertFalse(
                heartbeat_work(
                    session,
                    item_id=self.item_id,
                    worker_id="worker-1",
                )
            )
            paused = finish_work(
                session,
                item_id=self.item_id,
                worker_id="worker-1",
                succeeded=False,
            )
            self.assertEqual(
                paused.status,
                WorkStatus.PAUSED,
            )
            self.assertEqual(paused.attempts, 0)
            run = session.get(ResearchRun, self.run_id)
            self.assertEqual(run.status, RunStatus.PAUSED)

            resumed = request_run_resume(
                session,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
            )
            self.assertEqual(
                resumed.status,
                WorkStatus.QUEUED,
            )

    def test_early_finish_requeues_only_finalization(
        self,
    ) -> None:
        with SessionFactory() as session:
            session.add(
                ResearchTask(
                    run_id=self.run_id,
                    task_type="research",
                    question="Completed direction",
                    status=TaskStatus.COMPLETED,
                    priority=1,
                    input_data={},
                    output_data={
                        "task_question": "Completed direction",
                        "summary": "Saved result",
                        "claim_ids": [],
                    },
                )
            )
            item = session.get(WorkItem, self.item_id)
            item.status = WorkStatus.LEASED
            item.lease_owner = "worker-1"
            item.attempts = 1
            run = session.get(ResearchRun, self.run_id)
            run.status = RunStatus.RUNNING
            session.commit()

            requested = request_run_early_completion(
                session,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
            )
            self.assertTrue(requested.finish_requested)
            self.assertFalse(
                heartbeat_work(
                    session,
                    item_id=self.item_id,
                    worker_id="worker-1",
                )
            )
            finalization = finish_work(
                session,
                item_id=self.item_id,
                worker_id="worker-1",
                succeeded=False,
            )
            self.assertEqual(
                finalization.status,
                WorkStatus.QUEUED,
            )
            self.assertTrue(
                finalization.payload["finish_early"]
            )
            self.assertEqual(finalization.attempts, 0)
            finalization.status = WorkStatus.LEASED
            finalization.lease_owner = "worker-2"
            finalization.attempts = 1
            session.commit()
            self.assertTrue(
                heartbeat_work(
                    session,
                    item_id=self.item_id,
                    worker_id="worker-2",
                    allow_finish_requested=True,
                )
            )
            completed = finish_work(
                session,
                item_id=self.item_id,
                worker_id="worker-2",
                succeeded=True,
            )
            self.assertEqual(
                completed.status,
                WorkStatus.SUCCEEDED,
            )
            self.assertFalse(completed.finish_requested)

    def test_finish_requires_saved_direction(self) -> None:
        response = self.client.post(
            f"/api/v1/runs/{self.run_id}/finish",
            headers=self._headers("finish-run-control-0001"),
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(
            "before any research direction",
            response.json()["detail"],
        )


if __name__ == "__main__":
    unittest.main()
