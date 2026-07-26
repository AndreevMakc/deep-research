from __future__ import annotations

import logging
from typing import Any

from langchain_tavily import TavilySearch

from app.config import get_settings
from app.schemas.research_result import SearchSource


logger = logging.getLogger(__name__)


def create_search_tool() -> TavilySearch:
    settings = get_settings()

    if not settings.tavily_api_key:
        raise RuntimeError(
            "TAVILY_API_KEY is not configured"
        )

    return TavilySearch(
        tavily_api_key=settings.tavily_api_key,
        max_results=5,
        topic="general",
        search_depth="advanced",
        include_answer=False,
        include_raw_content=False,
    )


def normalize_search_results(
    raw_result: Any,
    query: str,
) -> list[SearchSource]:
    """
    Convert Tavily output to our internal SearchSource schema.

    The defensive checks are intentional: tool integrations may wrap
    their results in slightly different dictionary structures.
    """

    if isinstance(raw_result, dict):
        items = raw_result.get("results", [])
    elif isinstance(raw_result, list):
        items = raw_result
    else:
        raise TypeError(
            "Unexpected Tavily result type: "
            f"{type(raw_result).__name__}"
        )

    normalized: list[SearchSource] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()

        if not url or not content:
            continue

        raw_score = item.get("score")
        score: float | None

        try:
            score = (
                float(raw_score)
                if raw_score is not None
                else None
            )
        except (TypeError, ValueError):
            score = None

        if score is not None:
            score = max(0.0, min(1.0, score))

        normalized.append(
            SearchSource(
                title=str(
                    item.get("title")
                    or url
                ).strip(),
                url=url,
                content=content,
                relevance_score=score,
                query=query,
            )
        )

    return normalized


def search_web(
    query: str,
) -> list[SearchSource]:
    cleaned_query = query.strip()

    if not cleaned_query:
        raise ValueError(
            "Search query must not be empty"
        )

    logger.info(
        "Executing web search query length=%s",
        len(cleaned_query),
    )

    tool = create_search_tool()

    raw_result = tool.invoke(
        {
            "query": cleaned_query,
        }
    )

    results = normalize_search_results(
        raw_result=raw_result,
        query=cleaned_query,
    )

    logger.info(
        "Web search returned %s normalized results",
        len(results),
    )

    return results