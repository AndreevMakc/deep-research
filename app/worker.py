from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import uuid

from app.db.models import WorkItem
from app.db.session import SessionFactory
from app.queue import (
    claim_next_work,
    finish_work,
    heartbeat_work,
)


def default_worker_id() -> str:
    return (
        f"{socket.gethostname()}:{os.getpid()}:"
        f"{uuid.uuid4().hex[:8]}"
    )


def execute_work_item(
    item: WorkItem,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    if item.kind != "execute_research_run":
        raise RuntimeError(
            f"Unsupported work kind: {item.kind}"
        )

    command = [
        sys.executable,
        "-m",
        "app.main",
        "--resume",
        str(item.run_id),
    ]
    finish_early = bool(
        item.payload.get("finish_early")
    )

    if finish_early:
        command.append("--finish-early")

    process = subprocess.Popen(command)
    heartbeat_interval = min(
        5.0,
        max(1.0, lease_seconds / 3),
    )

    while process.poll() is None:
        time.sleep(heartbeat_interval)

        with SessionFactory() as session:
            owned = heartbeat_work(
                session,
                item_id=item.id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                allow_finish_requested=finish_early,
            )

        if owned:
            continue

        process.terminate()

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

        return False

    return process.returncode == 0


def run_once(
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool | None:
    with SessionFactory() as session:
        item = claim_next_work(
            session,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    if item is None:
        return None

    error: str | None = None

    try:
        succeeded = execute_work_item(
            item,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
    except Exception as exception:
        succeeded = False
        error = (
            f"{type(exception).__name__}: {exception}"
        )

    with SessionFactory() as session:
        try:
            finish_work(
                session,
                item_id=item.id,
                worker_id=worker_id,
                succeeded=succeeded,
                error=error,
            )
        except RuntimeError:
            return False

    return succeeded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Durable research queue worker."
    )
    parser.add_argument(
        "--worker-id",
        default=default_worker_id(),
    )
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=60,
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2,
    )
    parser.add_argument(
        "--once",
        action="store_true",
    )
    arguments = parser.parse_args(argv)

    while True:
        result = run_once(
            worker_id=arguments.worker_id,
            lease_seconds=max(
                10,
                arguments.lease_seconds,
            ),
        )

        if arguments.once:
            return 0 if result is not False else 1

        if result is None:
            time.sleep(max(0.1, arguments.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
