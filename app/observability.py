from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any, TypeVar

from sqlalchemy.exc import SQLAlchemyError

from app.db.models import EventStatus, OperationalEvent
from app.db.session import SessionFactory


logger = logging.getLogger("deep_research.events")
T = TypeVar("T")

_correlation_id: ContextVar[str | None] = ContextVar(
    "correlation_id",
    default=None,
)
_run_id: ContextVar[uuid.UUID | None] = ContextVar(
    "run_id",
    default=None,
)
_task_id: ContextVar[uuid.UUID | None] = ContextVar(
    "task_id",
    default=None,
)
_claim_id: ContextVar[uuid.UUID | None] = ContextVar(
    "claim_id",
    default=None,
)
_agent: ContextVar[str | None] = ContextVar(
    "agent",
    default=None,
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(
            record,
            "structured_event",
            None,
        )

        if payload is None:
            payload = {
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": record.getMessage(),
            }

        return json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )


def configure_structured_logging(
    level: str = "INFO",
) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    if any(
        getattr(handler, "_deep_research_json", False)
        for handler in root.handlers
    ):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._deep_research_json = True  # type: ignore[attr-defined]
    root.handlers.clear()
    root.addHandler(handler)


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _safe_metadata(
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    safe: dict[str, Any] = {}

    for key, value in (metadata or {}).items():
        lowered = key.lower()

        if any(
            secret in lowered
            for secret in (
                "api_key",
                "authorization",
                "password",
                "secret",
            )
        ):
            safe[key] = "[REDACTED]"
            continue

        if isinstance(value, str) and len(value) > 500:
            safe[key] = value[:500] + "…"
        elif isinstance(
            value,
            (str, int, float, bool, type(None)),
        ):
            safe[key] = value
        else:
            safe[key] = str(value)[:500]

    return safe


@contextmanager
def observability_context(
    *,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    claim_id: uuid.UUID | None = None,
    agent: str | None = None,
    correlation_id: str | None = None,
) -> Iterator[None]:
    tokens = (
        (_run_id, _run_id.set(run_id)),
        (_task_id, _task_id.set(task_id)),
        (_claim_id, _claim_id.set(claim_id)),
        (_agent, _agent.set(agent)),
        (
            _correlation_id,
            _correlation_id.set(
                correlation_id
                or (
                    str(run_id)
                    if run_id is not None
                    else uuid.uuid4().hex
                )
            ),
        ),
    )

    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def emit_event(
    *,
    operation: str,
    event_type: str,
    status: EventStatus,
    attempt: int | None = None,
    duration_ms: float | None = None,
    token_estimate: int = 0,
    estimated_cost_usd: float = 0,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    persist: bool = True,
) -> None:
    run_id = _run_id.get()
    correlation_id = (
        _correlation_id.get()
        or (str(run_id) if run_id else uuid.uuid4().hex)
    )
    safe_metadata = _safe_metadata(metadata)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": (
            "error"
            if status == EventStatus.FAILED
            else (
                "warning"
                if status == EventStatus.RETRYING
                else "info"
            )
        ),
        "correlation_id": correlation_id,
        "run_id": run_id,
        "task_id": _task_id.get(),
        "claim_id": _claim_id.get(),
        "agent": _agent.get(),
        "operation": operation,
        "event_type": event_type,
        "status": status.value,
        "attempt": attempt,
        "duration_ms": duration_ms,
        "token_estimate": token_estimate,
        "estimated_cost_usd": estimated_cost_usd,
        "error_code": error_code,
        "metadata": safe_metadata,
    }
    log_method = (
        logger.error
        if status == EventStatus.FAILED
        else (
            logger.warning
            if status == EventStatus.RETRYING
            else logger.info
        )
    )
    log_method(
        event_type,
        extra={"structured_event": payload},
    )

    if not persist or run_id is None:
        return

    try:
        with SessionFactory() as session:
            session.add(
                OperationalEvent(
                    run_id=run_id,
                    correlation_id=correlation_id,
                    task_id=_task_id.get(),
                    claim_id=_claim_id.get(),
                    agent=_agent.get(),
                    operation=operation,
                    event_type=event_type,
                    status=status,
                    attempt=attempt,
                    duration_ms=duration_ms,
                    token_estimate=token_estimate,
                    estimated_cost_usd=estimated_cost_usd,
                    error_code=error_code,
                    metadata_json=safe_metadata,
                )
            )
            session.commit()
    except SQLAlchemyError as error:
        logger.debug(
            "Telemetry persistence skipped: %s",
            error,
        )


@contextmanager
def operation_span(
    operation: str,
    *,
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    started = time.perf_counter()
    emit_event(
        operation=operation,
        event_type=event_type,
        status=EventStatus.STARTED,
        metadata=metadata,
    )

    try:
        yield
    except Exception as error:
        emit_event(
            operation=operation,
            event_type=event_type,
            status=EventStatus.FAILED,
            duration_ms=(
                time.perf_counter() - started
            )
            * 1000,
            error_code=type(error).__name__,
            metadata=metadata,
        )
        raise
    else:
        emit_event(
            operation=operation,
            event_type=event_type,
            status=EventStatus.SUCCEEDED,
            duration_ms=(
                time.perf_counter() - started
            )
            * 1000,
            metadata=metadata,
        )


def observed_node(
    node: Callable[[dict], T],
    *,
    agent: str,
) -> Callable[[dict], T]:
    @wraps(node)
    def wrapped(state: dict) -> T:
        run_id = _parse_uuid(state.get("run_id"))
        task_id = _parse_uuid(state.get("task_id"))
        claim_id = _parse_uuid(state.get("claim_id"))

        with observability_context(
            run_id=run_id,
            task_id=task_id,
            claim_id=claim_id,
            agent=agent,
            correlation_id=(
                str(run_id)
                if run_id is not None
                else None
            ),
        ):
            with operation_span(
                f"node.{agent}",
                event_type="node_execution",
            ):
                return node(state)

    return wrapped
