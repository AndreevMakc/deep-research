from __future__ import annotations

from builtins import BaseExceptionGroup
from dataclasses import asdict, dataclass
from typing import Any

import openai
from sqlalchemy.exc import OperationalError

from app.budget import RunLimitExceeded


@dataclass(frozen=True)
class UserError:
    """Safe, actionable description of an expected failure."""

    code: str
    message: str
    action: str
    retryable: bool
    request_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    def render(
        self,
        *,
        run_id: str | None = None,
    ) -> str:
        lines = [
            "Не удалось завершить исследование.",
            f"Причина: {self.message}",
            f"Что сделать: {self.action}",
        ]

        if run_id:
            lines.append(f"Run ID: {run_id}")

        if self.request_id:
            lines.append(
                "OpenAI request ID: "
                f"{self.request_id}"
            )

        return "\n".join(lines)


def _exception_candidates(
    error: BaseException,
) -> list[BaseException]:
    candidates: list[BaseException] = []
    pending = [error]
    visited: set[int] = set()

    while pending:
        current = pending.pop()

        if id(current) in visited:
            continue

        visited.add(id(current))
        candidates.append(current)

        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)

        if current.__cause__ is not None:
            pending.append(current.__cause__)

        if current.__context__ is not None:
            pending.append(current.__context__)

    return candidates


def _openai_error_code(
    error: openai.APIError,
) -> str | None:
    direct_code = getattr(error, "code", None)

    if direct_code:
        return str(direct_code)

    body = getattr(error, "body", None)

    if not isinstance(body, dict):
        return None

    nested_error = body.get("error")

    if isinstance(nested_error, dict):
        return _mapping_code(nested_error)

    return _mapping_code(body)


def _mapping_code(value: dict[str, Any]) -> str | None:
    code = value.get("code")
    return str(code) if code else None


def _request_id(
    error: openai.APIError,
) -> str | None:
    value = getattr(error, "request_id", None)
    return str(value) if value else None


def _classify_openai_error(
    error: openai.OpenAIError,
) -> UserError:
    if isinstance(error, openai.RateLimitError):
        if _openai_error_code(error) == "insufficient_quota":
            return UserError(
                code="openai_insufficient_quota",
                message=(
                    "на API-проекте OpenAI закончилась "
                    "доступная квота."
                ),
                action=(
                    "проверьте Billing и Usage в OpenAI "
                    "Platform, пополните баланс или используйте "
                    "другой API-проект."
                ),
                retryable=False,
                request_id=_request_id(error),
            )

        return UserError(
            code="openai_rate_limit",
            message=(
                "OpenAI временно ограничил частоту запросов."
            ),
            action=(
                "подождите немного и повторите запуск; при "
                "частых ошибках уменьшите параллелизм workers."
            ),
            retryable=True,
            request_id=_request_id(error),
        )

    if isinstance(error, openai.AuthenticationError):
        return UserError(
            code="openai_authentication",
            message=(
                "OpenAI отклонил API-ключ."
            ),
            action=(
                "проверьте OPENAI_API_KEY в .env и убедитесь, "
                "что ключ активен."
            ),
            retryable=False,
            request_id=_request_id(error),
        )

    if isinstance(error, openai.PermissionDeniedError):
        return UserError(
            code="openai_permission_denied",
            message=(
                "API-проекту запрещён доступ к запрошенной "
                "модели или операции."
            ),
            action=(
                "проверьте права проекта и значения "
                "RESEARCH_MODEL/WORKER_MODEL."
            ),
            retryable=False,
            request_id=_request_id(error),
        )

    if isinstance(error, openai.APITimeoutError):
        return UserError(
            code="openai_timeout",
            message=(
                "OpenAI не ответил за отведённое время."
            ),
            action=(
                "повторите запуск; если ошибка повторяется, "
                "проверьте сеть и уменьшите параллелизм."
            ),
            retryable=True,
        )

    if isinstance(error, openai.APIConnectionError):
        return UserError(
            code="openai_connection",
            message=(
                "не удалось установить соединение с OpenAI."
            ),
            action=(
                "проверьте интернет, VPN или proxy и повторите "
                "запуск."
            ),
            retryable=True,
        )

    if isinstance(error, openai.NotFoundError):
        return UserError(
            code="openai_not_found",
            message=(
                "OpenAI не нашёл запрошенную модель или ресурс."
            ),
            action=(
                "проверьте RESEARCH_MODEL и WORKER_MODEL "
                "в .env."
            ),
            retryable=False,
            request_id=_request_id(error),
        )

    if isinstance(error, openai.BadRequestError):
        return UserError(
            code="openai_bad_request",
            message=(
                "OpenAI отклонил параметры запроса."
            ),
            action=(
                "проверьте выбранную модель и совместимость "
                "structured output."
            ),
            retryable=False,
            request_id=_request_id(error),
        )

    if isinstance(error, openai.InternalServerError):
        return UserError(
            code="openai_server_error",
            message=(
                "на стороне OpenAI произошла временная ошибка."
            ),
            action=(
                "повторите запуск позже."
            ),
            retryable=True,
            request_id=_request_id(error),
        )

    return UserError(
        code="openai_api_error",
        message="OpenAI API вернул ошибку.",
        action=(
            "проверьте настройки API и повторите запуск."
        ),
        retryable=False,
        request_id=_request_id(error),
    )


def classify_expected_error(
    error: BaseException,
) -> UserError | None:
    for candidate in _exception_candidates(error):
        if isinstance(candidate, RunLimitExceeded):
            return UserError(
                code="run_limit_exceeded",
                message=str(candidate),
                action=(
                    "увеличьте лимит для нового run или "
                    "сократите объём исследования."
                ),
                retryable=False,
            )

        if isinstance(candidate, openai.OpenAIError):
            return _classify_openai_error(candidate)

        if isinstance(candidate, OperationalError):
            return UserError(
                code="database_unavailable",
                message=(
                    "приложение не может подключиться "
                    "к PostgreSQL."
                ),
                action=(
                    "запустите `docker compose up -d` и "
                    "`python -m app.db.migrate`."
                ),
                retryable=True,
            )

        if isinstance(candidate, RuntimeError):
            message = str(candidate)

            if "OPENAI_API_KEY is not configured" in message:
                return UserError(
                    code="openai_key_missing",
                    message=(
                        "OPENAI_API_KEY не настроен."
                    ),
                    action=(
                        "добавьте API-ключ в локальный .env."
                    ),
                    retryable=False,
                )

            if "TAVILY_API_KEY is not configured" in message:
                return UserError(
                    code="tavily_key_missing",
                    message=(
                        "TAVILY_API_KEY не настроен."
                    ),
                    action=(
                        "добавьте API-ключ Tavily "
                        "в локальный .env."
                    ),
                    retryable=False,
                )

            if (
                "MODEL" in message
                and "not configured" in message
            ):
                return UserError(
                    code="openai_model_missing",
                    message=(
                        "не настроена модель для одного "
                        "из агентов."
                    ),
                    action=(
                        "заполните RESEARCH_MODEL и при "
                        "необходимости WORKER_MODEL/"
                        "WRITER_MODEL в локальном .env."
                    ),
                    retryable=False,
                )

    return None
