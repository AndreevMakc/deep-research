import hashlib
import uuid
import unittest
from unittest.mock import patch

from app.agents.researcher import (
    PreparedSource,
    collect_search_sources,
    generate_research_result,
)
from app.schemas.research_result import (
    ResearchFinding,
    ResearchTaskResult,
    SearchSource,
)
from app.schemas.source_document import SourceDocument


SOURCE_URL = "https://example.com/evidence"
SOURCE_TEXT = (
    "Primary source heading\n"
    "The documented mechanism sends work to parallel workers.\n"
    "The results are combined through a reducer."
)


def make_search_source(
    url: str = SOURCE_URL,
    *,
    query: str = "test query",
    score: float = 0.8,
) -> SearchSource:
    return SearchSource(
        title="Test source",
        url=url,
        content="Parallel workers and reducer.",
        relevance_score=score,
        query=query,
    )


def make_document(
    url: str = SOURCE_URL,
    *,
    content: str = SOURCE_TEXT,
) -> SourceDocument:
    return SourceDocument(
        requested_url=url,
        url=url,
        canonical_url=url,
        title="Test source",
        content=content,
        content_hash=hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
        mime_type="text/html",
    )


def make_prepared_source(
    url: str = SOURCE_URL,
    *,
    content: str = SOURCE_TEXT,
) -> PreparedSource:
    return PreparedSource(
        source_id=uuid.uuid4(),
        source_snapshot_id=uuid.uuid4(),
        search_source=make_search_source(url),
        document=make_document(
            url,
            content=content,
        ),
    )


def make_result(
    url: str = SOURCE_URL,
    *,
    quote: str = (
        "The documented mechanism sends work "
        "to parallel workers."
    ),
    task_question: str = "A different question?",
) -> ResearchTaskResult:
    return ResearchTaskResult(
        task_question=task_question,
        summary=(
            "The stored source supports a draft answer "
            "about parallel execution."
        ),
        findings=[
            ResearchFinding(
                statement=(
                    "The mechanism sends work "
                    "to parallel workers."
                ),
                source_url=url,
                evidence_quote=quote,
                locator="Primary source heading",
                scope="The documented workflow.",
            )
        ],
        source_urls=[url],
    )


class FakeStructuredModel:
    def __init__(self, result: ResearchTaskResult):
        self.result = result
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.result


class FakeModel:
    def __init__(self, result: ResearchTaskResult):
        self.structured = FakeStructuredModel(result)
        self.schema = None
        self.method = None

    def with_structured_output(
        self,
        schema,
        method,
        strict,
    ):
        self.schema = schema
        self.method = method
        self.strict = strict
        return self.structured


class ResearcherTests(unittest.TestCase):
    def test_collects_and_deduplicates_search_sources(
        self,
    ) -> None:
        seen_queries: list[str] = []

        def fake_search(query: str) -> list[SearchSource]:
            seen_queries.append(query)
            return [
                make_search_source(
                    query=query,
                    score=(
                        0.9
                        if query == "second"
                        else 0.5
                    ),
                )
            ]

        sources = collect_search_sources(
            [" first ", "second"],
            search_fn=fake_search,
        )

        self.assertEqual(seen_queries, ["first", "second"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].query, "second")

    def test_generates_result_and_keeps_exact_task_question(
        self,
    ) -> None:
        task_question = (
            "What evidence does the source provide?"
        )
        fake_model = FakeModel(make_result())

        with patch(
            "app.agents.researcher.create_worker_model",
            return_value=fake_model,
        ):
            result = generate_research_result(
                task_question=task_question,
                sources=[make_prepared_source()],
            )

        self.assertEqual(result.task_question, task_question)
        self.assertEqual(fake_model.method, "json_schema")
        self.assertIs(
            fake_model.schema,
            ResearchTaskResult,
        )

    def test_rejects_url_absent_from_source_packet(
        self,
    ) -> None:
        invented_url = "https://example.com/invented"
        fake_model = FakeModel(
            make_result(url=invented_url)
        )

        with patch(
            "app.agents.researcher.create_worker_model",
            return_value=fake_model,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "absent from source packet",
            ):
                generate_research_result(
                    task_question=(
                        "What evidence does the source provide?"
                    ),
                    sources=[make_prepared_source()],
                )

    def test_rejects_quote_absent_from_stored_text(
        self,
    ) -> None:
        fake_model = FakeModel(
            make_result(
                quote=(
                    "This quote does not exist "
                    "in the source."
                )
            )
        )

        with patch(
            "app.agents.researcher.create_worker_model",
            return_value=fake_model,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "absent from stored source text",
            ):
                generate_research_result(
                    task_question=(
                        "What evidence does the source provide?"
                    ),
                    sources=[make_prepared_source()],
                )

    def test_fails_before_model_call_without_sources(
        self,
    ) -> None:
        with patch(
            "app.agents.researcher.create_worker_model",
        ) as model_factory:
            with self.assertRaisesRegex(
                ValueError,
                "persisted sources",
            ):
                generate_research_result(
                    task_question=(
                        "What evidence does the source provide?"
                    ),
                    sources=[],
                )

        model_factory.assert_not_called()

    def test_accepts_no_findings_when_sources_are_insufficient(
        self,
    ) -> None:
        task_question = (
            "What evidence does the source provide?"
        )
        result_without_findings = ResearchTaskResult(
            task_question=task_question,
            summary=(
                "The available material is insufficient "
                "to answer the research question."
            ),
            unanswered_questions=[
                "A relevant primary source is still needed."
            ],
        )
        fake_model = FakeModel(result_without_findings)

        with patch(
            "app.agents.researcher.create_worker_model",
            return_value=fake_model,
        ):
            result = generate_research_result(
                task_question=task_question,
                sources=[make_prepared_source()],
            )

        self.assertEqual(result.findings, [])
        self.assertEqual(result.source_urls, [])


if __name__ == "__main__":
    unittest.main()
