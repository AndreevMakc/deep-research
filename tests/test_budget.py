import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.budget import (
    RunLimitExceeded,
    _consume_run_budget,
    estimate_tokens,
)
from app.db.models import ResearchRun, RunStatus


def _run(**updates) -> ResearchRun:
    values = {
        "id": uuid.uuid4(),
        "question": "Budget test",
        "status": RunStatus.RUNNING,
        "started_at": datetime.now(timezone.utc),
        "max_external_requests": 3,
        "max_sources": 5,
        "max_claims": 5,
        "max_tokens": 100,
        "max_run_seconds": 60,
        "external_requests_used": 1,
        "tokens_used": 10,
    }
    values.update(updates)
    return ResearchRun(**values)


class RunBudgetTests(unittest.TestCase):
    def test_estimates_at_least_one_token(self) -> None:
        self.assertEqual(estimate_tokens(""), 1)
        self.assertEqual(estimate_tokens("12345"), 2)

    def test_consumes_budget_atomically(self) -> None:
        run = _run()
        session = MagicMock()
        session.scalar.return_value = run

        result = _consume_run_budget(
            session,
            run.id,
            external_requests=1,
            tokens=25,
        )

        self.assertIs(result, run)
        self.assertEqual(run.external_requests_used, 2)
        self.assertEqual(run.tokens_used, 35)
        session.commit.assert_called_once()

    def test_rejects_external_request_over_limit(self) -> None:
        run = _run(external_requests_used=3)
        session = MagicMock()
        session.scalar.return_value = run

        with self.assertRaises(RunLimitExceeded):
            _consume_run_budget(
                session,
                run.id,
                external_requests=1,
            )

        session.commit.assert_not_called()

    def test_rejects_token_over_limit(self) -> None:
        run = _run(tokens_used=95)
        session = MagicMock()
        session.scalar.return_value = run

        with self.assertRaises(RunLimitExceeded):
            _consume_run_budget(
                session,
                run.id,
                tokens=6,
            )

    def test_rejects_expired_run(self) -> None:
        run = _run(
            started_at=(
                datetime.now(timezone.utc)
                - timedelta(seconds=61)
            )
        )
        session = MagicMock()
        session.scalar.return_value = run

        with self.assertRaises(RunLimitExceeded):
            _consume_run_budget(session, run.id)


if __name__ == "__main__":
    unittest.main()
