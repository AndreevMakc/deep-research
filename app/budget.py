from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ResearchRun
from app.db.session import SessionFactory
from app.config import get_settings
from app.db.models import EventStatus
from app.observability import emit_event


class RunLimitExceeded(RuntimeError):
    """Raised before work would exceed a persisted run limit."""


def estimate_tokens(value: str) -> int:
    """Conservative provider-independent estimate for text input."""

    return max(1, math.ceil(len(value) / 4))


def _consume_run_budget(
    session: Session,
    run_id: uuid.UUID,
    *,
    external_requests: int = 0,
    tokens: int = 0,
) -> ResearchRun:
    run = session.scalar(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .with_for_update()
    )

    if run is None:
        raise RuntimeError(f"Research run not found: {run_id}")

    now = datetime.now(timezone.utc)
    started_at = run.started_at or now
    elapsed = (now - started_at).total_seconds()

    if elapsed > run.max_run_seconds:
        raise RunLimitExceeded(
            "Run time limit exceeded: "
            f"{elapsed:.1f}s/{run.max_run_seconds}s"
        )

    next_requests = (
        run.external_requests_used + external_requests
    )
    next_tokens = run.tokens_used + tokens

    if next_requests > run.max_external_requests:
        raise RunLimitExceeded(
            "Run external request limit exceeded: "
            f"{next_requests}/{run.max_external_requests}"
        )

    if next_tokens > run.max_tokens:
        raise RunLimitExceeded(
            "Run token limit exceeded: "
            f"{next_tokens}/{run.max_tokens}"
        )

    run.started_at = started_at
    run.external_requests_used = next_requests
    run.tokens_used = next_tokens
    session.commit()
    session.refresh(run)
    return run


def consume_run_budget(
    run_id: uuid.UUID,
    *,
    external_requests: int = 0,
    tokens: int = 0,
) -> None:
    if external_requests < 0 or tokens < 0:
        raise ValueError("Budget increments must be non-negative")

    with SessionFactory() as session:
        _consume_run_budget(
            session,
            run_id,
            external_requests=external_requests,
            tokens=tokens,
        )

    estimated_cost = (
        tokens
        / 1_000_000
        * get_settings().estimated_input_cost_per_1m_tokens_usd
    )
    emit_event(
        operation="run_budget",
        event_type="budget_consumed",
        status=EventStatus.INFO,
        token_estimate=tokens,
        estimated_cost_usd=estimated_cost,
        metadata={
            "external_requests": external_requests,
        },
    )


def check_run_time(run_id: uuid.UUID) -> None:
    consume_run_budget(run_id)
