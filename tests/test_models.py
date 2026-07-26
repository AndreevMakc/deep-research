from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.models import (
    create_planner_model,
    create_verifier_model,
    create_worker_model,
    create_writer_model,
)


def settings(**overrides: object) -> SimpleNamespace:
    values = {
        "llm_provider": "openai",
        "llm_api_key": "universal-key",
        "llm_base_url": None,
        "openai_api_key": None,
        "research_model": "planner-model",
        "worker_model": None,
        "verifier_model": None,
        "writer_model": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ModelConfigurationTests(unittest.TestCase):
    @patch("app.models.ChatGoogleGenerativeAI")
    @patch("app.models.get_settings")
    def test_google_uses_native_adapter(
        self,
        get_settings,
        chat_google,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="google",
            research_model="gemini-3.5-flash",
        )

        create_planner_model()

        chat_google.assert_called_once_with(
            model="gemini-3.5-flash",
            api_key="universal-key",
            temperature=0,
            retries=2,
            request_timeout=120,
        )

    @patch("app.models.ChatOpenRouter")
    @patch("app.models.get_settings")
    def test_openrouter_uses_native_adapter(
        self,
        get_settings,
        chat_openrouter,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="openrouter",
            research_model="openrouter/free",
        )

        create_planner_model()

        chat_openrouter.assert_called_once_with(
            model="openrouter/free",
            api_key="universal-key",
            temperature=0,
            max_retries=2,
            timeout=120_000,
        )

    @patch("app.models.ChatGroq")
    @patch("app.models.get_settings")
    def test_groq_uses_native_adapter(
        self,
        get_settings,
        chat_groq,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="groq",
            research_model="openai/gpt-oss-20b",
        )

        create_planner_model()

        chat_groq.assert_called_once_with(
            model="openai/gpt-oss-20b",
            api_key="universal-key",
            temperature=0,
            max_retries=2,
            timeout=120,
        )

    @patch("app.models.ChatOpenAI")
    @patch("app.models.get_settings")
    def test_custom_base_url_overrides_provider_default(
        self,
        get_settings,
        chat_openai,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="custom",
            llm_base_url="https://llm.example.test/v1",
        )

        create_planner_model()

        self.assertEqual(
            chat_openai.call_args.kwargs["base_url"],
            "https://llm.example.test/v1",
        )

    @patch("app.models.ChatOpenAI")
    @patch("app.models.get_settings")
    def test_legacy_openai_key_remains_supported(
        self,
        get_settings,
        chat_openai,
    ) -> None:
        get_settings.return_value = settings(
            llm_api_key=None,
            openai_api_key="legacy-key",
        )

        create_worker_model()

        self.assertEqual(
            chat_openai.call_args.kwargs["api_key"],
            "legacy-key",
        )

    @patch("app.models.ChatOpenAI")
    @patch("app.models.get_settings")
    def test_ollama_does_not_require_api_key(
        self,
        get_settings,
        chat_openai,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="ollama",
            llm_api_key=None,
        )

        create_writer_model()

        self.assertEqual(
            chat_openai.call_args.kwargs["api_key"],
            "not-needed",
        )
        self.assertEqual(
            chat_openai.call_args.kwargs["base_url"],
            "http://localhost:11434/v1",
        )

    @patch("app.models.ChatOpenAI")
    @patch("app.models.get_settings")
    def test_verifier_uses_dedicated_model(
        self,
        get_settings,
        chat_openai,
    ) -> None:
        get_settings.return_value = settings(
            worker_model="researcher-model",
            verifier_model="verifier-model",
        )

        create_verifier_model()

        self.assertEqual(
            chat_openai.call_args.kwargs["model"],
            "verifier-model",
        )

    @patch("app.models.ChatOpenAI")
    @patch("app.models.get_settings")
    def test_verifier_falls_back_to_worker_model(
        self,
        get_settings,
        chat_openai,
    ) -> None:
        get_settings.return_value = settings(
            worker_model="researcher-model",
        )

        create_verifier_model()

        self.assertEqual(
            chat_openai.call_args.kwargs["model"],
            "researcher-model",
        )

    @patch("app.models.get_settings")
    def test_remote_provider_requires_api_key(
        self,
        get_settings,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="groq",
            llm_api_key=None,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "LLM_API_KEY",
        ):
            create_planner_model()

    @patch("app.models.get_settings")
    def test_unknown_provider_requires_base_url(
        self,
        get_settings,
    ) -> None:
        get_settings.return_value = settings(
            llm_provider="custom",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "requires LLM_BASE_URL",
        ):
            create_planner_model()


if __name__ == "__main__":
    unittest.main()
