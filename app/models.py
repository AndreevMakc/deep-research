from langchain_openai import ChatOpenAI

from app.config import get_settings


def create_planner_model() -> ChatOpenAI:
    settings = get_settings()

    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured"
        )

    if not settings.research_model:
        raise RuntimeError(
            "RESEARCH_MODEL is not configured"
        )

    return ChatOpenAI(
        model=settings.research_model,
        api_key=settings.openai_api_key,
        temperature=0,
        max_retries=2,
        timeout=120,
    )


def create_worker_model() -> ChatOpenAI:
    settings = get_settings()

    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured"
        )

    model_name = (
        settings.worker_model
        or settings.research_model
    )

    if not model_name:
        raise RuntimeError(
            "WORKER_MODEL or RESEARCH_MODEL is not configured"
        )

    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        temperature=0,
        max_retries=2,
        timeout=120,
    )


def create_writer_model() -> ChatOpenAI:
    settings = get_settings()

    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured"
        )

    model_name = (
        settings.writer_model
        or settings.worker_model
        or settings.research_model
    )

    if not model_name:
        raise RuntimeError(
            "WRITER_MODEL, WORKER_MODEL, or "
            "RESEARCH_MODEL is not configured"
        )

    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        temperature=0,
        max_retries=2,
        timeout=120,
    )
