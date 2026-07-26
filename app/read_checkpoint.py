import sys

from app.checkpoint import postgres_checkpointer
from app.graph import build_graph

import json


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python -m app.read_checkpoint <run-id>"
        )

    run_id = sys.argv[1]

    config = {
        "configurable": {
            "thread_id": run_id,
        }
    }

    with postgres_checkpointer() as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        snapshot = graph.get_state(config)

    if not snapshot.values:
        raise SystemExit(
            f"No checkpoint found for run: {run_id}"
        )

    print("Checkpoint loaded")
    print(f"Next nodes: {snapshot.next}")
    print(f"Question: {snapshot.values['question']}")
    print(f"Plan items: {len(snapshot.values['plan'])}")
    print(f"Findings: {len(snapshot.values['findings'])}")
    print("\nReport:\n")
    print(snapshot.values["report"])
    print("\nPlan:\n")
    print(
        json.dumps(
            snapshot.values["plan"],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()