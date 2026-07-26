import io
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app.db.models import ClaimStatus, RunStatus, TaskStatus
from app.main import main, resolve_run_status


class MainErrorHandlingTests(unittest.TestCase):
    def test_resolves_completed_with_errors(
        self,
    ) -> None:
        status = resolve_run_status(
            [
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            ],
            [ClaimStatus.SUPPORTED],
        )

        self.assertEqual(
            status,
            RunStatus.COMPLETED_WITH_ERRORS,
        )

    def test_resolves_failed_when_no_task_completed(
        self,
    ) -> None:
        status = resolve_run_status(
            [TaskStatus.FAILED],
            [],
        )

        self.assertEqual(status, RunStatus.FAILED)

    @patch(
        "app.main.create_research_run",
        side_effect=OperationalError(
            "connection failed",
            params=None,
            orig=OSError("connection refused"),
        ),
    )
    @patch(
        "app.main.SessionFactory"
    )
    @patch(
        "app.main.sys.argv",
        ["app.main", "Тестовый вопрос"],
    )
    def test_reports_initial_database_connection_error(
        self,
        session_factory,
        create_run,
    ) -> None:
        session_factory.return_value.__enter__.return_value = (
            object()
        )
        stderr = io.StringIO()

        with patch("app.main.sys.stderr", stderr):
            exit_code = main()

        self.assertEqual(exit_code, 2)
        self.assertIn(
            "приложение не может подключиться к PostgreSQL",
            stderr.getvalue(),
        )
        self.assertNotIn("Traceback", stderr.getvalue())
        create_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
