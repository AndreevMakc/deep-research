import sys

from app.agents.planner import generate_research_plan


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()

    if not question:
        question = (
            "Как многоагентные системы могут повысить "
            "достоверность deep research?"
        )

    plan = generate_research_plan(question)

    print(plan.model_dump_json(indent=2))


if __name__ == "__main__":
    main()