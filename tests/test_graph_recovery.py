import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.db.models import TaskStatus
from app.graph import _bounded_node, create_plan
from app.state import (
    merge_findings,
    merge_unique,
    merge_verifications,
)


class GraphRecoveryTests(unittest.TestCase):
    def test_new_plan_receives_confirmed_research_input(
        self,
    ) -> None:
        run_id = uuid.uuid4()
        research_input = {
            "materials": [
                {
                    "role": "verify",
                    "url": "https://example.com/source",
                }
            ],
            "settings": {
                "effective": {"geography": "Россия"}
            },
        }
        plan = MagicMock()
        plan.model_dump.return_value = {
            "subquestions": []
        }
        plan.subquestions = []

        with (
            patch(
                "app.graph.SessionFactory",
                MagicMock(),
            ),
            patch(
                "app.graph.get_tasks_for_run",
                return_value=[],
            ),
            patch(
                "app.graph.generate_research_plan",
                return_value=plan,
            ) as generate_plan,
            patch(
                "app.graph.create_research_tasks",
                return_value=[],
            ),
        ):
            create_plan(
                {
                    "run_id": str(run_id),
                    "question": "Исследовать рынок",
                    "research_input": research_input,
                }
            )

        generate_plan.assert_called_once_with(
            question="Исследовать рынок",
            run_id=run_id,
            research_input=research_input,
        )

    def test_resume_selects_only_unfinished_tasks(
        self,
    ) -> None:
        completed_id = uuid.uuid4()
        failed_id = uuid.uuid4()
        completed = SimpleNamespace(
            id=completed_id,
            status=TaskStatus.COMPLETED,
            output_data={
                "task_question": "Completed question",
                "summary": "Completed summary",
                "claim_ids": ["claim-completed"],
            },
            input_data={"title": "Completed"},
            question="Completed question",
            priority=1,
        )
        failed = SimpleNamespace(
            id=failed_id,
            status=TaskStatus.FAILED,
            output_data={
                "error": {
                    "message": "temporary failure",
                }
            },
            input_data={"title": "Failed"},
            question="Failed question",
            priority=2,
        )
        session_factory = MagicMock()

        with (
            patch(
                "app.graph.SessionFactory",
                session_factory,
            ),
            patch(
                "app.graph.get_tasks_for_run",
                return_value=[completed, failed],
            ),
            patch(
                "app.graph.generate_research_plan",
            ) as generate_plan,
        ):
            result = create_plan(
                {
                    "run_id": str(uuid.uuid4()),
                    "question": "Resume this research run",
                    "plan": {"persisted": True},
                    "task_ids": [],
                    "findings": [],
                    "claim_ids": [],
                    "pending_claim_ids": [],
                    "verifications": [],
                    "report": "",
                    "report_json": {},
                }
            )

        generate_plan.assert_not_called()
        self.assertEqual(
            result["task_ids"],
            [str(failed_id)],
        )
        self.assertEqual(
            result["findings"][0]["task_id"],
            str(completed_id),
        )
        self.assertEqual(
            result["claim_ids"],
            ["claim-completed"],
        )

    def test_reducers_replace_replayed_worker_results(
        self,
    ) -> None:
        self.assertEqual(
            merge_unique(
                ["claim-1"],
                ["claim-1", "claim-2"],
            ),
            ["claim-1", "claim-2"],
        )
        findings = merge_findings(
            [
                {
                    "task_id": "task-1",
                    "result": {"error": {}},
                }
            ],
            [
                {
                    "task_id": "task-1",
                    "result": {"summary": "recovered"},
                }
            ],
        )
        verifications = merge_verifications(
            [
                {
                    "claim_id": "claim-1",
                    "error": {},
                }
            ],
            [
                {
                    "claim_id": "claim-1",
                    "verdict": "supported",
                }
            ],
        )

        self.assertEqual(
            findings,
            [
                {
                    "task_id": "task-1",
                    "result": {"summary": "recovered"},
                }
            ],
        )
        self.assertEqual(
            verifications,
            [
                {
                    "claim_id": "claim-1",
                    "verdict": "supported",
                }
            ],
        )

    def test_bounded_node_enforces_worker_limit(
        self,
    ) -> None:
        lock = threading.Lock()
        active = 0
        peak = 0

        def worker(state):
            nonlocal active, peak

            with lock:
                active += 1
                peak = max(peak, active)

            time.sleep(0.02)

            with lock:
                active -= 1

            return state

        bounded = _bounded_node(worker, limit=2)

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    bounded,
                    [{"value": index} for index in range(6)],
                )
            )

        self.assertEqual(len(results), 6)
        self.assertEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()
