from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

import httpx
from langchain_core.exceptions import OutputParserException
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import get_settings
from app.error_handling import classify_expected_error
from app.db.models import EventStatus
from app.observability import emit_event


logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class ExternalOutputValidationError(ValueError):
    """An external model returned structurally valid, unusable data."""


def is_retryable_error(error: BaseException) -> bool:
    pending: list[BaseException] = [error]
    visited: set[int] = set()

    while pending:
        current = pending.pop()

        if id(current) in visited:
            continue

        visited.add(id(current))
        user_error = classify_expected_error(current)

        if user_error is not None:
            return user_error.retryable

        if isinstance(
            current,
            (
                OutputParserException,
                ExternalOutputValidationError,
                httpx.TimeoutException,
                httpx.TransportError,
                socket.gaierror,
            ),
        ):
            return True

        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code

            if status == 429 or status >= 500:
                return True

        if current.__cause__ is not None:
            pending.append(current.__cause__)

        if current.__context__ is not None:
            pending.append(current.__context__)

    return False


def _log_retry(
    operation: str,
) -> Callable[[RetryCallState], None]:
    def log_attempt(retry_state: RetryCallState) -> None:
        error = (
            retry_state.outcome.exception()
            if retry_state.outcome is not None
            else None
        )
        logger.warning(
            "Retrying external operation=%s attempt=%s "
            "error=%s",
            operation,
            retry_state.attempt_number,
            error,
        )
        emit_event(
            operation=operation,
            event_type="external_call",
            status=EventStatus.RETRYING,
            attempt=retry_state.attempt_number,
            error_code=(
                type(error).__name__
                if error is not None
                else None
            ),
            metadata={
                "retryable": True,
            },
        )

    return log_attempt


def retry_external_call(
    operation: str,
    fn: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    settings = get_settings()
    attempts = max(1, settings.external_max_attempts)
    min_wait = max(0.0, settings.retry_min_wait_seconds)
    max_wait = max(
        min_wait,
        settings.retry_max_wait_seconds,
    )
    retrying = Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(
            initial=min_wait,
            max=max_wait,
        ),
        retry=retry_if_exception(is_retryable_error),
        before_sleep=_log_retry(operation),
        reraise=True,
    )

    attempt = 0

    def observed_call() -> T:
        nonlocal attempt
        attempt += 1
        started = time.perf_counter()
        emit_event(
            operation=operation,
            event_type="external_call",
            status=EventStatus.STARTED,
            attempt=attempt,
        )

        try:
            result = fn(*args, **kwargs)
        except Exception as error:
            emit_event(
                operation=operation,
                event_type="external_call",
                status=EventStatus.FAILED,
                attempt=attempt,
                duration_ms=(
                    time.perf_counter() - started
                )
                * 1000,
                error_code=type(error).__name__,
                metadata={
                    "retryable": is_retryable_error(
                        error
                    ),
                },
            )
            raise

        emit_event(
            operation=operation,
            event_type="external_call",
            status=EventStatus.SUCCEEDED,
            attempt=attempt,
            duration_ms=(
                time.perf_counter() - started
            )
            * 1000,
        )
        return result

    return retrying(observed_call)
