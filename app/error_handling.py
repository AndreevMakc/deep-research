from __future__ import annotations

from builtins import BaseExceptionGroup
from dataclasses import asdict, dataclass
from typing import Any

import openai
import groq
from google.genai import errors as google_errors
from openrouter import errors as openrouter_errors
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


def _classify_google_error(
    error: google_errors.APIError,
) -> UserError:
    status_code = getattr(error, "code", None)

    if status_code in {401, 403}:
        return UserError(
            code="google_authentication",
            message="Google Gemini отклонил API-ключ или доступ.",
            action=(
                "проверьте ключ и доступ модели в Google AI Studio."
            ),
            retryable=False,
        )

    if status_code == 429:
        return UserError(
            code="google_rate_limit",
            message="достигнут лимит запросов Google Gemini.",
            action=(
                "подождите до сброса RPM-квоты, уменьшите "
                "параллелизм или подключите billing."
            ),
            retryable=True,
        )

    if status_code is not None and status_code >= 500:
        return UserError(
            code="google_server_error",
            message="Google Gemini временно недоступен.",
            action="повторите запрос позже.",
            retryable=True,
        )

    return UserError(
        code="google_api_error",
        message="Google Gemini отклонил запрос.",
        action=(
            "проверьте имя модели, входные данные и "
            "ограничения Gemini API."
        ),
        retryable=False,
    )


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
    error: Any,
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
                "LLM-провайдер отклонил API-ключ."
            ),
            action=(
                "проверьте LLM_API_KEY (или устаревший "
                "OPENAI_API_KEY) в .env и убедитесь, что "
                "ключ активен."
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


def _classify_groq_error(
    error: groq.GroqError,
) -> UserError:
    request_id = _request_id(error)

    if isinstance(error, groq.AuthenticationError):
        return UserError(
            code="groq_authentication",
            message="Groq отклонил API-ключ.",
            action=(
                "проверьте LLM_API_KEY в .env и убедитесь, "
                "что ключ Groq активен."
            ),
            retryable=False,
            request_id=request_id,
        )

    if isinstance(error, groq.RateLimitError):
        return UserError(
            code="groq_rate_limit",
            message="Groq ограничил запрос по квоте или rate limit.",
            action=(
                "подождите и повторите запуск либо уменьшите "
                "контекст и параллелизм агентов."
            ),
            retryable=True,
            request_id=request_id,
        )

    if isinstance(
        error,
        (groq.BadRequestError, groq.UnprocessableEntityError),
    ):
        return UserError(
            code="groq_bad_request",
            message="Groq отклонил параметры запроса.",
            action=(
                "проверьте модель, structured output и размер "
                "контекста."
            ),
            retryable=False,
            request_id=request_id,
        )

    if isinstance(
        error,
        (groq.APITimeoutError, groq.APIConnectionError),
    ):
        return UserError(
            code="groq_connection",
            message="не удалось получить ответ от Groq.",
            action="проверьте сеть и повторите запуск.",
            retryable=True,
            request_id=request_id,
        )

    if isinstance(
        error,
        (groq.InternalServerError,),
    ):
        return UserError(
            code="groq_server_error",
            message="на стороне Groq произошла временная ошибка.",
            action="повторите запуск позже.",
            retryable=True,
            request_id=request_id,
        )

    return UserError(
        code="groq_api_error",
        message="Groq API вернул ошибку.",
        action="проверьте настройки провайдера и повторите запуск.",
        retryable=False,
        request_id=request_id,
    )


def _classify_openrouter_error(
    error: openrouter_errors.OpenRouterError,
) -> UserError:
    if isinstance(
        error,
        openrouter_errors.UnauthorizedResponseError,
    ):
        return UserError(
            code="openrouter_authentication",
            message="OpenRouter отклонил API-ключ.",
            action=(
                "проверьте LLM_API_KEY в .env и убедитесь, "
                "что ключ OpenRouter активен."
            ),
            retryable=False,
        )

    if isinstance(
        error,
        openrouter_errors.PaymentRequiredResponseError,
    ):
        return UserError(
            code="openrouter_balance",
            message="на балансе OpenRouter недостаточно средств.",
            action="пополните баланс или выберите бесплатную модель.",
            retryable=False,
        )

    if isinstance(
        error,
        openrouter_errors.TooManyRequestsResponseError,
    ):
        return UserError(
            code="openrouter_rate_limit",
            message="OpenRouter временно ограничил запросы.",
            action=(
                "подождите и повторите запуск либо уменьшите "
                "параллелизм агентов."
            ),
            retryable=True,
        )

    if isinstance(
        error,
        (
            openrouter_errors.BadRequestResponseError,
            openrouter_errors.UnprocessableEntityResponseError,
            openrouter_errors.PayloadTooLargeResponseError,
        ),
    ):
        return UserError(
            code="openrouter_bad_request",
            message="OpenRouter отклонил параметры запроса.",
            action=(
                "проверьте модель, structured output и размер "
                "контекста."
            ),
            retryable=False,
        )

    if isinstance(
        error,
        (
            openrouter_errors.RequestTimeoutResponseError,
            openrouter_errors.EdgeNetworkTimeoutResponseError,
            openrouter_errors.NoResponseError,
        ),
    ):
        return UserError(
            code="openrouter_connection",
            message="не удалось получить ответ от OpenRouter.",
            action="проверьте сеть и повторите запуск.",
            retryable=True,
        )

    if isinstance(
        error,
        (
            openrouter_errors.InternalServerResponseError,
            openrouter_errors.BadGatewayResponseError,
            openrouter_errors.ServiceUnavailableResponseError,
            openrouter_errors.ProviderOverloadedResponseError,
        ),
    ):
        return UserError(
            code="openrouter_server_error",
            message=(
                "OpenRouter или выбранный upstream-провайдер "
                "временно недоступен."
            ),
            action="повторите запуск позже.",
            retryable=True,
        )

    return UserError(
        code="openrouter_api_error",
        message="OpenRouter API вернул ошибку.",
        action="проверьте настройки провайдера и повторите запуск.",
        retryable=False,
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

        if isinstance(candidate, groq.GroqError):
            return _classify_groq_error(candidate)

        if isinstance(candidate, google_errors.APIError):
            return _classify_google_error(candidate)

        if isinstance(
            candidate,
            openrouter_errors.OpenRouterError,
        ):
            return _classify_openrouter_error(candidate)

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

            if (
                "LLM_API_KEY or OPENAI_API_KEY "
                "is not configured"
            ) in message:
                return UserError(
                    code="openai_key_missing",
                    message=(
                        "LLM_API_KEY не настроен."
                    ),
                    action=(
                        "добавьте ключ провайдера в "
                        "LLM_API_KEY в локальном .env."
                    ),
                    retryable=False,
                )

            if (
                "LLM_PROVIDER" in message
                and "requires LLM_BASE_URL" in message
            ):
                return UserError(
                    code="llm_provider_invalid",
                    message=(
                        "для выбранного LLM-провайдера "
                        "не настроен URL API."
                    ),
                    action=(
                        "используйте openai, openrouter, groq, "
                        "google или ollama либо заполните "
                        "LLM_BASE_URL в локальном .env."
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
                        "VERIFIER_MODEL/WRITER_MODEL "
                        "в локальном .env."
                    ),
                    retryable=False,
                )

    return None
