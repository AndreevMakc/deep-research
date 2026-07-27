from __future__ import annotations

import unittest
import uuid
import warnings
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import app
from app.config import get_settings
from app.db.models import (
    ApiIdentity,
    ApiRole,
    BrowserSession,
    ResearchReport,
    ResearchRun,
    ReviewerIdentity,
    RunStatus,
    Tenant,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_password_identity,
    create_tenant,
    reviewer_subject,
)


class BrowserAuthenticationTests(unittest.TestCase):
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
        self.slug = f"auth-{suffix}"
        self.admin_password = "Admin password 123!"

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Authentication test",
            )
            admin = create_password_identity(
                session,
                tenant=tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password=self.admin_password,
            )
            self.tenant_id = tenant.id
            self.admin_id = admin.id

        self.client = TestClient(
            app,
            base_url="https://testserver",
        )

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

    def _login(
        self,
        client: TestClient,
        *,
        login: str = "admin",
        password: str | None = None,
    ):
        return client.post(
            "/api/v1/auth/login",
            json={
                "tenant": self.slug,
                "login": login,
                "password": (
                    password or self.admin_password
                ),
            },
        )

    @staticmethod
    def _csrf_headers(client: TestClient) -> dict:
        cookie_name = get_settings().csrf_cookie_name
        return {
            "X-CSRF-Token": client.cookies.get(
                cookie_name
            )
        }

    def _create_researcher(
        self,
        *,
        login: str = "researcher",
        password: str = "Researcher password 123!",
    ) -> uuid.UUID:
        logged_in = self._login(self.client)
        self.assertEqual(logged_in.status_code, 200)
        created = self.client.post(
            "/api/v1/admin/accounts",
            headers=self._csrf_headers(self.client),
            json={
                "login": login,
                "role": "researcher",
                "password": password,
            },
        )
        self.assertEqual(created.status_code, 201)
        return uuid.UUID(created.json()["id"])

    def test_login_persists_and_logout_revokes_session(
        self,
    ) -> None:
        invalid = self._login(
            self.client,
            password="Wrong password 123!",
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(
            invalid.json()["detail"],
            "Invalid credentials",
        )

        logged_in = self._login(self.client)
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.json()["login"], "admin")
        self.assertEqual(logged_in.json()["role"], "admin")
        set_cookie = logged_in.headers.get_list(
            "set-cookie"
        )
        session_cookie = next(
            value
            for value in set_cookie
            if value.startswith(
                get_settings().session_cookie_name + "="
            )
        )
        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("SameSite=strict", session_cookie)
        self.assertIn("Secure", session_cookie)
        cookie_name = get_settings().session_cookie_name
        self.assertTrue(
            self.client.cookies.get(cookie_name)
        )

        with SessionFactory() as session:
            admin = session.get(
                ApiIdentity,
                self.admin_id,
            )
            self.assertIsNotNone(admin)
            self.assertNotIn(
                self.admin_password,
                admin.password_hash,
            )

        restored = self.client.get(
            "/api/v1/auth/session"
        )
        self.assertEqual(restored.status_code, 200)

        missing_csrf = self.client.post(
            "/api/v1/auth/logout"
        )
        self.assertEqual(missing_csrf.status_code, 403)

        logged_out = self.client.post(
            "/api/v1/auth/logout",
            headers=self._csrf_headers(self.client),
        )
        self.assertEqual(logged_out.status_code, 204)
        self.assertEqual(
            self.client.get(
                "/api/v1/auth/session"
            ).status_code,
            401,
        )

    def test_run_records_author_and_provenance_is_gated(
        self,
    ) -> None:
        researcher_password = "Researcher password 123!"
        self._create_researcher(
            password=researcher_password
        )
        researcher_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            logged_in = self._login(
                researcher_client,
                login="researcher",
                password=researcher_password,
            )
            self.assertEqual(logged_in.status_code, 200)
            created = researcher_client.post(
                "/api/v1/runs",
                headers={
                    **self._csrf_headers(
                        researcher_client
                    ),
                    "Idempotency-Key": (
                        "browser-create-run-0001"
                    ),
                },
                json={
                    "question": (
                        "Who created this research run?"
                    )
                },
            )
            self.assertEqual(created.status_code, 202)
            run_id = created.json()["run_id"]
            runs = researcher_client.get(
                "/api/v1/runs"
            )
            self.assertEqual(runs.status_code, 200)
            self.assertEqual(
                runs.json()[0]["author"],
                "researcher",
            )
            denied = researcher_client.get(
                f"/api/v1/runs/{run_id}/provenance"
            )
            self.assertEqual(denied.status_code, 403)
        finally:
            researcher_client.close()

    def test_library_supports_title_archive_and_ownership(
        self,
    ) -> None:
        password = "Researcher password 123!"
        self._create_researcher(password=password)
        self._create_researcher(
            login="other-researcher",
            password="Other researcher password 123!",
        )
        researcher_client = TestClient(
            app,
            base_url="https://testserver",
        )
        other_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            self.assertEqual(
                self._login(
                    researcher_client,
                    login="researcher",
                    password=password,
                ).status_code,
                200,
            )
            self.assertEqual(
                self._login(
                    other_client,
                    login="other-researcher",
                    password=(
                        "Other researcher password 123!"
                    ),
                ).status_code,
                200,
            )
            self.assertEqual(
                researcher_client.get(
                    "/api/v1/runs"
                ).json(),
                [],
            )
            invalid = researcher_client.post(
                "/api/v1/runs",
                headers={
                    **self._csrf_headers(
                        researcher_client
                    ),
                    "Idempotency-Key": (
                        "library-invalid-run-0001"
                    ),
                },
                json={"question": "   "},
            )
            self.assertEqual(invalid.status_code, 422)
            created = researcher_client.post(
                "/api/v1/runs",
                headers={
                    **self._csrf_headers(
                        researcher_client
                    ),
                    "Idempotency-Key": (
                        "library-create-run-0001"
                    ),
                },
                json={
                    "question": (
                        "Какие факторы влияют на выбор?"
                    )
                },
            )
            self.assertEqual(created.status_code, 202)
            self.assertEqual(
                created.json()["title"],
                "Какие факторы влияют на выбор",
            )
            run_id = created.json()["run_id"]
            second = researcher_client.post(
                "/api/v1/runs",
                headers={
                    **self._csrf_headers(
                        researcher_client
                    ),
                    "Idempotency-Key": (
                        "library-create-run-0002"
                    ),
                },
                json={"question": "Второе исследование"},
            )
            self.assertEqual(second.status_code, 202)
            updated = researcher_client.patch(
                f"/api/v1/runs/{run_id}",
                headers=self._csrf_headers(
                    researcher_client
                ),
                json={"title": "Новый заголовок"},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(
                updated.json()["title"],
                "Новый заголовок",
            )
            self.assertTrue(updated.json()["can_manage"])

            denied = other_client.patch(
                f"/api/v1/runs/{run_id}",
                headers=self._csrf_headers(other_client),
                json={"archived": True},
            )
            self.assertEqual(denied.status_code, 403)

            archived = researcher_client.patch(
                f"/api/v1/runs/{run_id}",
                headers=self._csrf_headers(
                    researcher_client
                ),
                json={"archived": True},
            )
            self.assertEqual(
                archived.json()["group"],
                "archived",
            )
            library = researcher_client.get(
                "/api/v1/runs"
            )
            self.assertEqual(library.status_code, 200)
            self.assertEqual(
                library.json()[0]["id"],
                run_id,
            )
            self.assertEqual(
                library.json()[0]["group"],
                "archived",
            )
        finally:
            researcher_client.close()
            other_client.close()

    def test_unread_result_is_per_user(
        self,
    ) -> None:
        password = "Researcher password 123!"
        self._create_researcher(password=password)
        researcher_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            self.assertEqual(
                self._login(
                    researcher_client,
                    login="researcher",
                    password=password,
                ).status_code,
                200,
            )
            created = researcher_client.post(
                "/api/v1/runs",
                headers={
                    **self._csrf_headers(
                        researcher_client
                    ),
                    "Idempotency-Key": (
                        "library-unread-run-0001"
                    ),
                },
                json={"question": "Готов ли результат?"},
            )
            self.assertEqual(created.status_code, 202)
            run_id = uuid.UUID(created.json()["run_id"])

            with SessionFactory() as session:
                run = session.get(ResearchRun, run_id)
                self.assertIsNotNone(run)
                run.status = RunStatus.COMPLETED
                run.updated_at = datetime.now(timezone.utc)
                session.add(
                    ResearchReport(
                        run_id=run.id,
                        markdown_path="report.md",
                        json_path="report.json",
                        markdown_hash="a" * 64,
                        json_hash="b" * 64,
                        result_json={"summary": "Ready"},
                    )
                )
                session.commit()

            researcher_library = researcher_client.get(
                "/api/v1/runs"
            ).json()
            admin_library = self.client.get(
                "/api/v1/runs"
            ).json()
            self.assertTrue(
                researcher_library[0]["unread_result"]
            )
            self.assertTrue(
                admin_library[0]["unread_result"]
            )
            self.assertEqual(
                researcher_library[0]["version_count"],
                1,
            )

            marked = researcher_client.post(
                f"/api/v1/runs/{run_id}/read",
                headers=self._csrf_headers(
                    researcher_client
                ),
            )
            self.assertEqual(marked.status_code, 200)
            self.assertFalse(
                researcher_client.get(
                    "/api/v1/runs"
                ).json()[0]["unread_result"]
            )
            self.assertTrue(
                self.client.get(
                    "/api/v1/runs"
                ).json()[0]["unread_result"]
            )
        finally:
            researcher_client.close()

    def test_password_reset_revokes_existing_sessions(
        self,
    ) -> None:
        old_password = "Researcher password 123!"
        new_password = "Replacement password 456!"
        researcher_id = self._create_researcher(
            password=old_password
        )
        researcher_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            self.assertEqual(
                self._login(
                    researcher_client,
                    login="researcher",
                    password=old_password,
                ).status_code,
                200,
            )
            reset = self.client.post(
                (
                    "/api/v1/admin/accounts/"
                    f"{researcher_id}/reset-password"
                ),
                headers=self._csrf_headers(self.client),
                json={"password": new_password},
            )
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(
                researcher_client.get(
                    "/api/v1/auth/session"
                ).status_code,
                401,
            )
            self.assertEqual(
                self._login(
                    researcher_client,
                    login="researcher",
                    password=old_password,
                ).status_code,
                401,
            )
            self.assertEqual(
                self._login(
                    researcher_client,
                    login="researcher",
                    password=new_password,
                ).status_code,
                200,
            )
        finally:
            researcher_client.close()

    def test_expired_session_and_cross_tenant_reset(
        self,
    ) -> None:
        self.assertEqual(
            self._login(self.client).status_code,
            200,
        )

        with SessionFactory() as session:
            browser_session = session.scalar(
                select(BrowserSession).where(
                    BrowserSession.identity_id
                    == self.admin_id
                )
            )
            self.assertIsNotNone(browser_session)
            browser_session.expires_at = (
                datetime.now(timezone.utc)
                - timedelta(seconds=1)
            )
            session.commit()

        self.assertEqual(
            self.client.get(
                "/api/v1/auth/session"
            ).status_code,
            401,
        )

        other_suffix = uuid.uuid4().hex[:10]
        other_slug = f"other-{other_suffix}"

        with SessionFactory() as session:
            other_tenant = create_tenant(
                session,
                slug=other_slug,
                name="Other tenant",
            )
            other_admin = create_password_identity(
                session,
                tenant=other_tenant,
                subject="admin",
                role=ApiRole.ADMIN,
                password="Other admin password 123!",
            )
            other_tenant_id = other_tenant.id
            other_admin_id = other_admin.id

        admin_client = TestClient(
            app,
            base_url="https://testserver",
        )

        try:
            self.assertEqual(
                self._login(admin_client).status_code,
                200,
            )
            denied = admin_client.post(
                (
                    "/api/v1/admin/accounts/"
                    f"{other_admin_id}/reset-password"
                ),
                headers=self._csrf_headers(admin_client),
                json={"password": "Cannot change this 123!"},
            )
            self.assertEqual(denied.status_code, 404)
        finally:
            admin_client.close()

            with SessionFactory() as session:
                other_tenant = session.get(
                    Tenant,
                    other_tenant_id,
                )

                if other_tenant is not None:
                    session.delete(other_tenant)

                reviewer = session.scalar(
                    select(ReviewerIdentity).where(
                        ReviewerIdentity.subject
                        == reviewer_subject(
                            other_slug,
                            "admin",
                        )
                    )
                )

                if reviewer is not None:
                    session.delete(reviewer)

                session.commit()


if __name__ == "__main__":
    unittest.main()
