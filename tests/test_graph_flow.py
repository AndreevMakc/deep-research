import unittest
from unittest.mock import patch

from app.graph import build_graph


class GraphFlowTests(unittest.TestCase):
    def test_parallel_research_results_are_joined_in_task_order(
        self,
    ) -> None:
        task_ids = ["task-first", "task-second"]
        verifier_inputs = []

        def fake_create_plan(_state):
            return {
                "plan": {
                    "subquestions": [],
                },
                "task_ids": task_ids,
            }

        def fake_researcher(state):
            task_id = state["task_id"]
            return {
                "claim_ids": [f"claim-for-{task_id}"],
                "findings": [
                    {
                        "task_id": task_id,
                        "result": {
                            "task_question": (
                                f"Question for {task_id}"
                            ),
                            "summary": (
                                f"Summary for {task_id}"
                            ),
                        },
                    }
                ]
            }

        def fake_collect_pending_claims(state):
            return {
                "pending_claim_ids": list(
                    state["claim_ids"]
                )
            }

        def fake_verifier(state):
            claim_id = state["claim_id"]
            verifier_inputs.append(claim_id)
            return {
                "verifications": [
                    {
                        "claim_id": claim_id,
                        "verdict": "supported",
                    }
                ]
            }

        def fake_writer(state):
            task_order = {
                task_id: index
                for index, task_id in enumerate(
                    task_ids
                )
            }
            findings = sorted(
                state["findings"],
                key=lambda item: task_order[
                    item["task_id"]
                ],
            )
            return {
                "report": "\n".join(
                    item["result"]["task_question"]
                    for item in findings
                ),
                "report_json": {},
            }

        with (
            patch(
                "app.graph.create_plan",
                new=fake_create_plan,
            ),
            patch(
                "app.graph.research_task_node",
                new=fake_researcher,
            ),
            patch(
                "app.graph.collect_pending_claims",
                new=fake_collect_pending_claims,
            ),
            patch(
                "app.graph.verifier_task_node",
                new=fake_verifier,
            ),
            patch(
                "app.graph.writer_node",
                new=fake_writer,
            ),
        ):
            graph = build_graph()
            result = graph.invoke(
                {
                    "run_id": "run-test",
                    "question": "Test research question",
                    "plan": {},
                    "task_ids": [],
                    "findings": [],
                    "claim_ids": [],
                    "pending_claim_ids": [],
                    "verifications": [],
                    "report": "",
                    "report_json": {},
                },
                config={
                    "max_concurrency": 2,
                },
            )

        self.assertEqual(
            {
                finding["task_id"]
                for finding in result["findings"]
            },
            set(task_ids),
        )
        self.assertLess(
            result["report"].index(
                "Question for task-first"
            ),
            result["report"].index(
                "Question for task-second"
            ),
        )
        self.assertEqual(
            set(verifier_inputs),
            {
                "claim-for-task-first",
                "claim-for-task-second",
            },
        )
        self.assertEqual(
            len(result["verifications"]),
            2,
        )

    def test_resume_with_no_pending_work_reaches_writer(
        self,
    ) -> None:
        def fake_create_plan(_state):
            return {
                "plan": {"persisted": True},
                "task_ids": [],
                "findings": [
                    {
                        "task_id": "completed-task",
                        "result": {
                            "task_question": (
                                "Previously completed question"
                            ),
                            "summary": (
                                "Previously completed summary"
                            ),
                        },
                    }
                ],
                "claim_ids": [],
            }

        def fake_collect_pending_claims(_state):
            return {"pending_claim_ids": []}

        def fake_writer(state):
            return {
                "report": (
                    state["findings"][0]["result"][
                        "summary"
                    ]
                ),
                "report_json": {},
            }

        with (
            patch(
                "app.graph.create_plan",
                new=fake_create_plan,
            ),
            patch(
                "app.graph.collect_pending_claims",
                new=fake_collect_pending_claims,
            ),
            patch(
                "app.graph.writer_node",
                new=fake_writer,
            ),
        ):
            graph = build_graph()
            result = graph.invoke(
                {
                    "run_id": "run-test",
                    "question": "Resume completed research",
                    "plan": {},
                    "task_ids": [],
                    "findings": [],
                    "claim_ids": [],
                    "pending_claim_ids": [],
                    "verifications": [],
                    "report": "",
                    "report_json": {},
                }
            )

        self.assertIn(
            "Previously completed summary",
            result["report"],
        )
        self.assertEqual(
            result["pending_claim_ids"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
