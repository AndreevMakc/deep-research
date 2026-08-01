from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    expect,
    sync_playwright,
)
from sqlalchemy import func, select

from app.db.models import (
    ApiRole,
    Claim,
    ClaimStatus,
    ResearchDraft,
    ResearchReport,
    ResearchRun,
    ReviewerIdentity,
    RunStatus,
    Source,
    SourceSnapshot,
    Tenant,
    Verification,
    VerificationVerdict,
    WorkItem,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_password_identity,
    create_tenant,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _available_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


class DashboardBrowserTests(unittest.TestCase):
    server: subprocess.Popen
    playwright: Playwright
    browser: Browser

    @classmethod
    def setUpClass(cls) -> None:
        cls.port = _available_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment["SESSION_COOKIE_SECURE"] = "false"
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--log-level",
                "warning",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            cls._wait_for_server()
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(
                headless=True
            )
        except Exception:
            if hasattr(cls, "playwright"):
                cls.playwright.stop()

            cls._stop_server()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "browser"):
            cls.browser.close()

        if hasattr(cls, "playwright"):
            cls.playwright.stop()

        cls._stop_server()

    @classmethod
    def _wait_for_server(cls) -> None:
        deadline = time.monotonic() + 15
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError(
                    "Browser test server exited before startup"
                )

            try:
                with urllib.request.urlopen(
                    f"{cls.base_url}/health/live",
                    timeout=1,
                ) as response:
                    if response.status == 200:
                        return
            except Exception as error:
                last_error = error
                time.sleep(0.1)

        raise RuntimeError(
            "Browser test server did not become ready"
        ) from last_error

    @classmethod
    def _stop_server(cls) -> None:
        if not hasattr(cls, "server"):
            return

        if cls.server.poll() is None:
            cls.server.terminate()

            try:
                cls.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.slug = f"browser-{suffix}"
        self.login = "admin"
        self.password = "Browser admin password 123!"

        with SessionFactory() as session:
            tenant = create_tenant(
                session,
                slug=self.slug,
                name="Browser end-to-end test",
            )
            identity = create_password_identity(
                session,
                tenant=tenant,
                subject=self.login,
                role=ApiRole.ADMIN,
                password=self.password,
            )
            self.tenant_id = tenant.id
            self.identity_id = identity.id

        self.context: BrowserContext = (
            self.browser.new_context()
        )
        self.page: Page = self.context.new_page()
        self.page.set_default_timeout(10_000)

    def tearDown(self) -> None:
        self.context.close()

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

    def _login(self) -> None:
        self.page.goto(
            f"{self.base_url}/dashboard",
            wait_until="domcontentloaded",
        )
        self.page.locator("#tenant").fill(self.slug)
        self.page.locator("#login").fill(self.login)
        self.page.locator("#password").fill(self.password)
        self.page.get_by_role(
            "button",
            name="Войти",
        ).click()
        expect(
            self.page.locator("#workspace")
        ).to_be_visible()

    def test_login_empty_library_draft_refresh_and_run(
        self,
    ) -> None:
        question = (
            "Какие факторы влияют на выбор платформы "
            "для российских B2B-команд за 2025 год "
            "по стоимости владения и безопасности?"
        )
        self._login()
        expect(self.page.locator("#runs")).to_contain_text(
            "Нет активных исследований"
        )

        self.page.locator("#question").fill(question)
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-question")
        ).to_have_text(question)
        expect(
            self.page.locator("#draft-scope")
        ).to_contain_text(question)

        self.page.get_by_role(
            "button",
            name="Изменить детали",
        ).click()
        expect(
            self.page.locator("#draft-edit-form")
        ).to_be_visible()
        self.page.get_by_role(
            "button",
            name="Вернуться",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()

        edited_scope = (
            "Сравнить варианты для небольшой команды"
        )
        edited_period = "Данные за последние два года"
        edited_assumptions = [
            "Команда работает удалённо",
            "Важна стоимость владения",
        ]
        self.page.get_by_role(
            "button",
            name="Изменить детали",
        ).click()
        self.page.locator("#draft-scope-input").fill(
            edited_scope
        )
        self.page.locator("#draft-period-input").fill(
            edited_period
        )
        self.page.locator(
            "#draft-assumptions-input"
        ).fill("\n".join(edited_assumptions))
        self.page.get_by_role(
            "button",
            name="Сохранить детали",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-scope")
        ).to_have_text(edited_scope)
        expect(
            self.page.locator("#draft-period")
        ).to_have_text(edited_period)

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

        self.page.reload(wait_until="domcontentloaded")
        expect(
            self.page.locator("#login-view")
        ).to_be_hidden()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-question")
        ).to_have_text(question)
        expect(
            self.page.locator("#draft-scope")
        ).to_have_text(edited_scope)
        expect(
            self.page.locator("#draft-assumptions")
        ).to_contain_text(edited_assumptions[1])

        self.page.get_by_role(
            "button",
            name="Начать исследование",
        ).click()
        run_card = self.page.locator(".run-card").filter(
            has_text="Какие факторы влияют на выбор платформы"
        )
        expect(run_card).to_have_count(1)
        expect(run_card).to_contain_text("В очереди")
        expect(self.page.locator("#details")).to_contain_text(
            question
        )

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
            self.assertEqual(
                session.scalar(
                    select(func.count(WorkItem.id)).where(
                        WorkItem.tenant_id == self.tenant_id
                    )
                ),
                1,
            )

    def test_edit_conflict_keeps_unsaved_values(
        self,
    ) -> None:
        self._login()
        self.page.locator("#question").fill(
            "Как выбрать формат исследования для "
            "российских B2B-команд за 2025 год "
            "по стоимости и качеству итогового отчёта?"
        )
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()
        self.page.get_by_role(
            "button",
            name="Изменить детали",
        ).click()

        unsaved_scope = "Пользовательская версия охвата"
        self.page.locator("#draft-scope-input").fill(
            unsaved_scope
        )

        with SessionFactory() as session:
            draft = session.scalar(
                select(ResearchDraft).where(
                    ResearchDraft.tenant_id
                    == self.tenant_id
                )
            )
            self.assertIsNotNone(draft)
            draft.scope = "Изменение из другой вкладки"
            draft.revision += 1
            session.commit()

        self.page.get_by_role(
            "button",
            name="Сохранить детали",
        ).click()
        expect(self.page.locator("#message")).to_contain_text(
            "изменён в другой вкладке"
        )
        expect(
            self.page.locator("#draft-edit-form")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-scope-input")
        ).to_have_value(unsaved_scope)

        self.page.get_by_role(
            "button",
            name="Сохранить детали",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-scope")
        ).to_have_text(unsaved_scope)

        with SessionFactory() as session:
            draft = session.scalar(
                select(ResearchDraft).where(
                    ResearchDraft.tenant_id
                    == self.tenant_id
                )
            )
            self.assertIsNotNone(draft)
            self.assertEqual(draft.scope, unsaved_scope)
            self.assertEqual(draft.revision, 3)

    def test_reads_partial_editorial_report_and_citation(
        self,
    ) -> None:
        with SessionFactory() as session:
            run = ResearchRun(
                tenant_id=self.tenant_id,
                created_by_identity_id=self.identity_id,
                question="Какой результат подтверждён?",
                title="Редакционный отчёт",
                status=RunStatus.COMPLETED_WITH_ERRORS,
            )
            session.add(run)
            session.flush()
            source_record = Source(
                run_id=run.id,
                url="https://example.com/evidence",
                canonical_url=(
                    "https://example.com/evidence"
                ),
                title="Проверенный источник",
                publisher="Example",
            )
            session.add(source_record)
            session.flush()
            snapshot_record = SourceSnapshot(
                source_id=source_record.id,
                run_id=run.id,
                final_url=source_record.canonical_url,
                content_hash="e" * 64,
                mime_type="text/plain",
                local_path="unused-in-browser-test.txt",
                http_status=200,
                content_length=54,
                metadata_json={},
            )
            session.add(snapshot_record)
            session.flush()
            claim_record = Claim(
                run_id=run.id,
                source_snapshot_id=snapshot_record.id,
                text=(
                    "Подтверждён доступный частичный результат."
                ),
                evidence_quote=(
                    "Точная цитата подтверждает доступный результат."
                ),
                quote_start=0,
                quote_end=49,
                locator={"section": "Результат"},
                scope="Частичный результат",
                status=ClaimStatus.PARTIALLY_SUPPORTED,
                created_by_agent="researcher-v1",
            )
            session.add(claim_record)
            session.flush()
            session.add(
                Verification(
                    claim_id=claim_record.id,
                    verifier_agent="verifier-v1",
                    verdict=(
                        VerificationVerdict
                        .PARTIALLY_SUPPORTED
                    ),
                    confidence=0.82,
                    reason=(
                        "Цитата прямо подтверждает узкую "
                        "часть вывода."
                    ),
                    checked_source_ids=[
                        str(source_record.id)
                    ],
                )
            )
            claim_id = str(claim_record.id)
            statement = {
                "text": "Подтверждён доступный частичный результат.",
                "claim_ids": [claim_id],
                "qualification": (
                    "Один дополнительный источник недоступен."
                ),
            }
            source = {
                "citation_label": "C1",
                "claim_id": claim_id,
                "source_snapshot_id": str(snapshot_record.id),
                "source_url": "https://example.com/evidence",
                "source_title": "Проверенный источник",
                "source_publisher": "Example",
                "source_published_at": (
                    "2026-07-20T00:00:00Z"
                ),
                "source_retrieved_at": (
                    "2026-07-30T00:00:00Z"
                ),
                "evidence_quote": (
                    "Точная цитата подтверждает доступный результат."
                ),
                "verdict": "partially_supported",
                "confidence": 0.82,
                "verification_reason": (
                    "Цитата прямо подтверждает узкую часть вывода."
                ),
            }
            session.add(
                ResearchReport(
                    run_id=run.id,
                    markdown_path="report.md",
                    json_path="report.json",
                    markdown_hash="a" * 64,
                    json_hash="b" * 64,
                    result_json={
                        "run_id": str(run.id),
                        "question": run.question,
                        "direct_answer": statement,
                        "key_findings": [
                            {
                                "title": "Доступный результат",
                                "statement": statement,
                            }
                        ],
                        "short_answer": [],
                        "sections": [
                            {
                                "heading": "Полный анализ",
                                "statements": [statement],
                            }
                        ],
                        "limitations": [
                            "Не все источники были доступны."
                        ],
                        "contradictions": [],
                        "unanswered_questions": [
                            "Что содержал недоступный источник?"
                        ],
                        "sources": [source],
                        "overall_confidence": 0.82,
                        "quality_summary": {
                            "confirmed_claims": 0,
                            "limited_claims": 1,
                            "contradicted_claims": 0,
                            "unsupported_claims": 0,
                            "source_count": 1,
                            "overall_confidence": 0.82,
                            "caveats": [
                                "Не все источники были доступны."
                            ],
                        },
                    },
                )
            )
            run_id = str(run.id)
            session.commit()

        self._login()
        self.page.get_by_role(
            "button",
            name="Редакционный отчёт",
        ).click()
        expect(self.page.locator(".partial-banner")).to_be_visible()
        expect(self.page.locator("#report-answer")).to_contain_text(
            "Подтверждён доступный частичный результат."
        )
        expect(self.page.locator("#report-quality")).to_contain_text(
            "82%"
        )
        expect(self.page.locator(".evidence-badge").first).to_have_text(
            "Ограничено"
        )

        citation = self.page.get_by_role(
            "button",
            name="Открыть источник C1",
        ).first
        citation.click()
        expect(self.page.locator("#source-drawer")).to_be_visible()
        expect(self.page.locator("#source-drawer")).to_contain_text(
            "Точная цитата подтверждает доступный результат."
        )
        expect(self.page.locator("#source-drawer")).to_contain_text(
            "Цитата прямо подтверждает узкую часть вывода."
        )
        self.page.locator(
            ".recheck-panel textarea"
        ).fill("Источник мог устареть.")
        self.page.get_by_role(
            "button",
            name="Проверить этот вывод",
        ).click()
        expect(self.page.locator(".recheck-status")).to_contain_text(
            "Перепроверяется"
        )
        self.page.get_by_role(
            "button",
            name="Закрыть",
        ).click()

        self.page.get_by_role(
            "button",
            name="Фокусный режим",
        ).click()
        expect(self.page.locator("#library-panel")).to_be_hidden()
        self.page.evaluate("window.scrollTo(0, 300)")
        self.page.wait_for_timeout(50)
        saved = self.page.evaluate(
            f"localStorage.getItem('deep-research:reading:{run_id}')"
        )
        self.assertIsNotNone(saved)

    def test_materials_and_advanced_settings_reach_run(
        self,
    ) -> None:
        self._login()
        self.page.locator("#question").fill(
            "Сравнить PostgreSQL и MySQL для российских "
            "B2B-команд за 2025 год по стоимости "
            "владения и безопасности."
        )
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()

        self.page.locator("#material-text").fill(
            "Внутренний контекст для планирования"
        )
        self.page.locator(
            "#material-text-form button"
        ).click()
        expect(
            self.page.locator("#material-list")
        ).to_contain_text("Вставленный текст")
        expect(
            self.page.locator("#material-list")
        ).to_contain_text("проверить независимо")

        self.page.locator("#material-url").fill(
            "https://example.com/primary"
        )
        self.page.locator("#material-role").select_option(
            "primary_source"
        )
        self.page.locator(
            "#material-url-form button"
        ).click()
        expect(
            self.page.locator("#material-list")
        ).to_contain_text("первичный источник")

        self.page.locator(
            ".material-item",
            has_text="Вставленный текст",
        ).get_by_role(
            "button",
            name="Удалить",
        ).click()
        expect(
            self.page.locator("#material-list")
        ).not_to_contain_text("Вставленный текст")

        expect(
            self.page.locator("#advanced-settings")
        ).to_be_visible()
        self.page.locator(
            "#advanced-settings summary"
        ).click()
        self.page.locator("#settings-geography").fill(
            "Россия"
        )
        self.page.locator(
            "#settings-form .primary-button"
        ).click()
        expect(
            self.page.locator("#settings-overrides")
        ).to_contain_text("geography")

        self.page.get_by_role(
            "button",
            name="Начать исследование",
        ).click()
        expect(self.page.locator(".run-card")).to_have_count(1)

        with SessionFactory() as session:
            work_item = session.scalar(
                select(WorkItem).where(
                    WorkItem.tenant_id == self.tenant_id
                )
            )
            self.assertIsNotNone(work_item)
            research_input = work_item.payload[
                "research_input"
            ]
            self.assertEqual(
                len(research_input["materials"]),
                1,
            )
            self.assertEqual(
                research_input["materials"][0]["role"],
                "primary_source",
            )
            self.assertEqual(
                research_input["settings"]["overrides"],
                {"geography": "Россия"},
            )

    def test_clarification_progress_recovers_after_reload(
        self,
    ) -> None:
        self._login()
        self.page.locator("#question").fill(
            "Какая платформа лучше?"
        )
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(
            self.page.locator("#clarification-card")
        ).to_be_visible()
        expect(
            self.page.locator("#notifications-badge")
        ).to_have_text("1")
        expect(
            self.page.locator("#clarification-progress")
        ).to_have_text("Шаг 1 из 3")

        first_option = self.page.locator(
            "#clarification-options .choice-button"
        ).first
        first_option.click()
        expect(
            self.page.locator("#clarification-answer")
        ).not_to_have_value("")
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(
            self.page.locator("#clarification-progress")
        ).to_have_text("Шаг 2 из 3")
        expect(
            self.page.locator("#notifications-badge")
        ).to_have_text("1")

        self.page.reload(wait_until="domcontentloaded")
        expect(
            self.page.locator("#login-view")
        ).to_be_hidden()
        expect(
            self.page.locator("#clarification-progress")
        ).to_have_text("Шаг 2 из 3")
        self.page.get_by_role(
            "button",
            name="Пропустить",
        ).click()
        expect(
            self.page.locator("#clarification-progress")
        ).to_have_text("Шаг 3 из 3")

        self.page.locator("#clarification-answer").fill(
            "Последние 12 месяцев"
        )
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(
            self.page.locator("#draft-card")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-period")
        ).to_have_text("Последние 12 месяцев")

        with SessionFactory() as session:
            draft = session.scalar(
                select(ResearchDraft).where(
                    ResearchDraft.tenant_id
                    == self.tenant_id
                )
            )
            self.assertIsNotNone(draft)
            self.assertEqual(draft.clarification_index, 3)
            self.assertEqual(
                len(draft.clarification_answers),
                3,
            )
            self.assertEqual(draft.revision, 4)
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchRun.id)).where(
                        ResearchRun.tenant_id
                        == self.tenant_id
                    )
                ),
                0,
            )

    def test_draft_error_is_visible_without_creating_run(
        self,
    ) -> None:
        self._login()

        def fail_draft(route, request) -> None:
            if request.method == "POST":
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "detail": (
                                "Исследование временно недоступно"
                            )
                        },
                        ensure_ascii=False,
                    ),
                )
            else:
                route.continue_()

        self.page.route(
            "**/api/v1/research-drafts",
            fail_draft,
        )
        self.page.locator("#question").fill(
            "Какие риски нужно проверить?"
        )
        self.page.get_by_role(
            "button",
            name="Продолжить",
        ).click()
        expect(self.page.locator("#message")).to_have_text(
            "Исследование временно недоступно"
        )
        expect(
            self.page.locator("#research-form")
        ).to_be_visible()
        expect(
            self.page.locator("#draft-card")
        ).to_be_hidden()

        with SessionFactory() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count(ResearchDraft.id)).where(
                        ResearchDraft.tenant_id
                        == self.tenant_id
                    )
                ),
                0,
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


if __name__ == "__main__":
    unittest.main()
