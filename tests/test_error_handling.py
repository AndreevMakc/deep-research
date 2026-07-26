from builtins import ExceptionGroup
import unittest

import httpx
import groq
import openai
from google.genai import errors as google_errors
from openrouter import errors as openrouter_errors

from app.error_handling import (
    classify_expected_error,
)


def api_response(
    status_code: int,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={
            "x-request-id": "req_test_123",
        },
        request=httpx.Request(
            "POST",
            "https://api.openai.com/v1/chat/completions",
        ),
    )


class ErrorHandlingTests(unittest.TestCase):
    def test_classifies_google_rate_limit_as_retryable(
        self,
    ) -> None:
        error = google_errors.ClientError(
            429,
            {
                "error": {
                    "message": "quota exceeded",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )

        result = classify_expected_error(error)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.code, "google_rate_limit")
        self.assertTrue(result.retryable)

    def test_classifies_insufficient_quota(
        self,
    ) -> None:
        error = openai.RateLimitError(
            "Quota exceeded",
            response=api_response(429),
            body={
                "message": "Quota exceeded",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            },
        )

        result = classify_expected_error(error)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.code,
            "openai_insufficient_quota",
        )
        self.assertFalse(result.retryable)
        self.assertEqual(
            result.request_id,
            "req_test_123",
        )
        self.assertNotIn(
            "Traceback",
            result.render(run_id="run-test"),
        )

    def test_classifies_temporary_rate_limit(
        self,
    ) -> None:
        error = openai.RateLimitError(
            "Too many requests",
            response=api_response(429),
            body={
                "message": "Too many requests",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            },
        )

        result = classify_expected_error(error)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.code,
            "openai_rate_limit",
        )
        self.assertTrue(result.retryable)

    def test_classifies_authentication_error(
        self,
    ) -> None:
        error = openai.AuthenticationError(
            "Invalid key",
            response=api_response(401),
            body={
                "code": "invalid_api_key",
            },
        )

        result = classify_expected_error(error)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.code,
            "openai_authentication",
        )

    def test_finds_error_inside_exception_group(
        self,
    ) -> None:
        request = httpx.Request(
            "POST",
            "https://api.openai.com/v1/chat/completions",
        )
        group = ExceptionGroup(
            "parallel workers failed",
            [
                ValueError("unrelated"),
                openai.APITimeoutError(request),
            ],
        )

        result = classify_expected_error(group)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.code,
            "openai_timeout",
        )
        self.assertTrue(result.retryable)

    def test_does_not_hide_unexpected_error(
        self,
    ) -> None:
        result = classify_expected_error(
            ValueError("programming error")
        )

        self.assertIsNone(result)

    def test_classifies_missing_writer_model(
        self,
    ) -> None:
        result = classify_expected_error(
            RuntimeError(
                "WRITER_MODEL, WORKER_MODEL, or "
                "RESEARCH_MODEL is not configured"
            )
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.code,
            "openai_model_missing",
        )

    def test_classifies_missing_llm_key(
        self,
    ) -> None:
        result = classify_expected_error(
            RuntimeError(
                "LLM_API_KEY or OPENAI_API_KEY "
                "is not configured"
            )
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.code, "openai_key_missing")
        self.assertIn("LLM_API_KEY", result.message)

    def test_classifies_custom_provider_without_url(
        self,
    ) -> None:
        result = classify_expected_error(
            RuntimeError(
                "LLM_PROVIDER 'custom' requires LLM_BASE_URL"
            )
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.code, "llm_provider_invalid")

    def test_classifies_groq_bad_request(
        self,
    ) -> None:
        error = groq.BadRequestError(
            "Unsupported response format",
            response=api_response(400),
            body={"message": "Unsupported response format"},
        )

        result = classify_expected_error(error)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.code, "groq_bad_request")

    def test_classifies_openrouter_api_error(
        self,
    ) -> None:
        error = openrouter_errors.OpenRouterError(
            "Provider failed",
            api_response(500),
        )

        result = classify_expected_error(error)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.code, "openrouter_api_error")


if __name__ == "__main__":
    unittest.main()
