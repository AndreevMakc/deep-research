import sys

from app.tools.web_search import search_web


def main() -> None:
    query = " ".join(sys.argv[1:]).strip()

    if not query:
        query = (
            "LangGraph PostgreSQL checkpointing "
            "official documentation"
        )

    results = search_web(query)

    print(f"Found: {len(results)} sources\n")

    for index, source in enumerate(
        results,
        start=1,
    ):
        print(f"{index}. {source.title}")
        print(f"   URL: {source.url}")
        print(
            f"   Score: {source.relevance_score}"
        )
        print(
            f"   Content: {source.content[:300]}"
        )
        print()


if __name__ == "__main__":
    main()