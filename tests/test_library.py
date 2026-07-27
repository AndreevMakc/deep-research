import unittest
from datetime import datetime, timezone

from app.db.models import ResearchRun, RunStatus
from app.library import generate_run_title, library_group


class ResearchLibraryTests(unittest.TestCase):
    def test_generates_compact_title_from_question(
        self,
    ) -> None:
        self.assertEqual(
            generate_run_title(
                "  Какие   факторы влияют на выбор?  "
            ),
            "Какие факторы влияют на выбор",
        )
        title = generate_run_title(
            "Очень длинный вопрос " * 20
        )
        self.assertLessEqual(len(title), 80)
        self.assertTrue(title.endswith("…"))
        self.assertEqual(
            generate_run_title("???"),
            "Новое исследование",
        )

    def test_groups_active_ready_and_archived_runs(
        self,
    ) -> None:
        run = ResearchRun(
            question="Question",
            title="Question",
            status=RunStatus.CREATED,
        )
        self.assertEqual(library_group(run), "active")

        run.status = RunStatus.COMPLETED
        self.assertEqual(library_group(run), "ready")

        run.archived_at = datetime.now(timezone.utc)
        self.assertEqual(library_group(run), "archived")


if __name__ == "__main__":
    unittest.main()
