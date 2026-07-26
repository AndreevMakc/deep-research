import unittest
import uuid
from unittest.mock import patch

from app.db.models import EventStatus
from app.observability import (
    _safe_metadata,
    observed_node,
)


class ObservabilityTests(unittest.TestCase):
    def test_redacts_secrets_and_truncates_values(
        self,
    ) -> None:
        metadata = _safe_metadata(
            {
                "api_key": "secret-value",
                "password_hint": "secret-value",
                "query": "x" * 600,
                "count": 3,
            }
        )
        self.assertEqual(metadata["api_key"], "[REDACTED]")
        self.assertEqual(
            metadata["password_hint"],
            "[REDACTED]",
        )
        self.assertTrue(metadata["query"].endswith("…"))
        self.assertEqual(metadata["count"], 3)

    def test_observed_node_emits_start_and_success(
        self,
    ) -> None:
        run_id = uuid.uuid4()

        with patch(
            "app.observability.emit_event"
        ) as emit:
            wrapped = observed_node(
                lambda state: {"ok": state["run_id"]},
                agent="test-agent",
            )
            result = wrapped({"run_id": str(run_id)})

        self.assertEqual(result["ok"], str(run_id))
        statuses = [
            call.kwargs["status"]
            for call in emit.call_args_list
        ]
        self.assertEqual(
            statuses,
            [EventStatus.STARTED, EventStatus.SUCCEEDED],
        )

    def test_observed_node_emits_failure(self) -> None:
        def fail(_state):
            raise ValueError("broken")

        with patch(
            "app.observability.emit_event"
        ) as emit:
            wrapped = observed_node(
                fail,
                agent="test-agent",
            )

            with self.assertRaises(ValueError):
                wrapped({"run_id": str(uuid.uuid4())})

        self.assertEqual(
            emit.call_args_list[-1].kwargs["status"],
            EventStatus.FAILED,
        )


if __name__ == "__main__":
    unittest.main()
