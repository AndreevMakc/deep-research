from __future__ import annotations

import re

from app.db.models import ResearchRun, RunStatus


ACTIVE_RUN_STATUSES = {
    RunStatus.CREATED,
    RunStatus.RUNNING,
    RunStatus.CANCEL_REQUESTED,
}


def generate_run_title(
    question: str,
    *,
    max_length: int = 80,
) -> str:
    normalized = re.sub(r"\s+", " ", question).strip()
    normalized = normalized.rstrip(" ?!.")

    if not normalized:
        return "Новое исследование"

    if len(normalized) <= max_length:
        return normalized

    candidate = normalized[: max_length - 1].rstrip()
    boundary = candidate.rfind(" ")

    if boundary >= max_length // 2:
        candidate = candidate[:boundary]

    return candidate.rstrip(" ,;:-") + "…"


def library_group(run: ResearchRun) -> str:
    if run.archived_at is not None:
        return "archived"

    if run.status in ACTIVE_RUN_STATUSES:
        return "active"

    return "ready"
