from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from app.config import get_settings


PROVIDER_BASE_URLS = {
    "openai": None,
    "ollama": "http://localhost:11434/v1",
}


def _create_model(
    model_name: str | None,
    missing_model_message: str,
) -> BaseChatModel:
    settings = get_settings()
    provider = settings.llm_provider.strip().lower()

    if not model_name:
        raise RuntimeError(missing_model_message)

    known_providers = {
        *PROVIDER_BASE_URLS,
        "google",
        "openrouter",
        "groq",
    }
    if (
        provider not in known_providers
        and not settings.llm_base_url
    ):
        raise RuntimeError(
            f"LLM_PROVIDER {provider!r} requires LLM_BASE_URL"
        )

    api_key = settings.llm_api_key or settings.openai_api_key
    if not api_key and provider != "ollama":
        raise RuntimeError(
            "LLM_API_KEY or OPENAI_API_KEY is not configured"
        )

    if provider == "google":
        return ChatGoogleGenerativeAI(
            model=model_name,
            api_key=api_key,
            temperature=0,
            retries=2,
            request_timeout=120,
        )

    model_kwargs: dict[str, object] = {
        "model": model_name,
        "api_key": api_key or "not-needed",
        "temperature": 0,
        "max_retries": 2,
    }

    if settings.llm_base_url:
        model_kwargs["base_url"] = settings.llm_base_url

    if provider == "openrouter":
        return ChatOpenRouter(
            **model_kwargs,
            timeout=120_000,
        )

    if provider == "groq":
        return ChatGroq(
            **model_kwargs,
            timeout=120,
        )

    base_url = PROVIDER_BASE_URLS.get(provider)
    if base_url and "base_url" not in model_kwargs:
        model_kwargs["base_url"] = base_url

    return ChatOpenAI(
        **model_kwargs,
        timeout=120,
    )


def create_planner_model() -> BaseChatModel:
    settings = get_settings()

    return _create_model(
        settings.research_model,
        "RESEARCH_MODEL is not configured",
    )


def create_worker_model() -> BaseChatModel:
    settings = get_settings()

    model_name = (
        settings.worker_model
        or settings.research_model
    )

    return _create_model(
        model_name,
        "WORKER_MODEL or RESEARCH_MODEL is not configured",
    )


def create_verifier_model() -> BaseChatModel:
    settings = get_settings()

    model_name = (
        settings.verifier_model
        or settings.worker_model
        or settings.research_model
    )

    return _create_model(
        model_name,
        (
            "VERIFIER_MODEL, WORKER_MODEL, or "
            "RESEARCH_MODEL is not configured"
        ),
    )


def create_writer_model() -> BaseChatModel:
    settings = get_settings()

    model_name = (
        settings.writer_model
        or settings.worker_model
        or settings.research_model
    )

    return _create_model(
        model_name,
        (
            "WRITER_MODEL, WORKER_MODEL, or "
            "RESEARCH_MODEL is not configured"
        ),
    )
