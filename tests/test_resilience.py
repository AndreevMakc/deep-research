import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from langchain_core.exceptions import OutputParserException

from app.resilience import (
    ExternalOutputValidationError,
    retry_external_call,
)


class ResilienceTests(unittest.TestCase):
    def test_retries_semantically_invalid_external_output(
        self,
    ) -> None:
        attempts = 0

        def flaky_call() -> str:
            nonlocal attempts
            attempts += 1

            if attempts == 1:
                raise ExternalOutputValidationError(
                    "invalid evidence"
                )

            return "ok"

        with patch(
            "app.resilience.get_settings",
            return_value=SimpleNamespace(
                external_max_attempts=2,
                retry_min_wait_seconds=0,
                retry_max_wait_seconds=0,
            ),
        ):
            result = retry_external_call(
                "researcher_llm",
                flaky_call,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)

    def test_retries_structured_output_parse_failure(
        self,
    ) -> None:
        attempts = 0

        def flaky_call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OutputParserException(
                    "invalid structured output"
                )
            return "ok"

        with patch(
            "app.resilience.get_settings",
            return_value=SimpleNamespace(
                external_max_attempts=2,
                retry_min_wait_seconds=0,
                retry_max_wait_seconds=0,
            ),
        ):
            result = retry_external_call(
                "structured_llm",
                flaky_call,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)

    def test_retries_transient_transport_error(
        self,
    ) -> None:
        attempts = 0

        def flaky_call() -> str:
            nonlocal attempts
            attempts += 1

            if attempts == 1:
                raise httpx.ReadTimeout(
                    "temporary timeout"
                )

            return "ok"

        settings = SimpleNamespace(
            external_max_attempts=3,
            retry_min_wait_seconds=0,
            retry_max_wait_seconds=0,
        )

        with (
            patch(
                "app.resilience.get_settings",
                return_value=settings,
            ),
            self.assertLogs(
                "app.resilience",
                level="WARNING",
            ),
        ):
            result = retry_external_call(
                "test_operation",
                flaky_call,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)

    def test_does_not_retry_programming_error(
        self,
    ) -> None:
        attempts = 0

        def broken_call() -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("invalid code path")

        settings = SimpleNamespace(
            external_max_attempts=3,
            retry_min_wait_seconds=0,
            retry_max_wait_seconds=0,
        )

        with patch(
            "app.resilience.get_settings",
            return_value=settings,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "invalid code path",
            ):
                retry_external_call(
                    "test_operation",
                    broken_call,
                )

        self.assertEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
