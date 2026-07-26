from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable

from langchain_core.messages import HumanMessage, SystemMessage

from app.budget import consume_run_budget, estimate_tokens
from app.db.models import Claim, VerificationVerdict
from app.db.repositories import (
    create_verification,
    get_claim,
)
from app.db.session import SessionFactory
from app.error_handling import classify_expected_error
from app.models import create_verifier_model
from app.prompts import load_prompt
from app.resilience import retry_external_call
from app.schemas.verification import (
    ClaimVerificationPacket,
    VerificationResult,
)
from app.source_store import read_source_snapshot_content
from app.state import VerificationWorkerState


logger = logging.getLogger(__name__)

VERIFIER_AGENT = "verifier-v1"
CONTEXT_CHARS = 1200
NUMBER_PATTERN = re.compile(
    r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?:\s?%)?"
)


def _result(
    verdict: VerificationVerdict,
    reason: str,
    confidence: float = 1.0,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict,
        confidence=confidence,
        reason=reason,
    )


def prepare_verification_packet(
    claim: Claim,
) -> tuple[
    ClaimVerificationPacket | None,
    VerificationResult | None,
]:
    snapshot = claim.source_snapshot

    if snapshot is None:
        return None, _result(
            VerificationVerdict.SOURCE_UNAVAILABLE,
            "У claim отсутствует сохранённый snapshot источника.",
        )

    try:
        content = read_source_snapshot_content(snapshot)
    except (OSError, RuntimeError, UnicodeError) as error:
        return None, _result(
            VerificationVerdict.SOURCE_UNAVAILABLE,
            f"Сохранённый snapshot недоступен или повреждён: {error}",
        )

    quote = claim.evidence_quote
    start = claim.quote_start
    end = claim.quote_end

    if (
        not quote
        or start is None
        or end is None
        or start < 0
        or end < start
        or content[start:end] != quote
    ):
        return None, _result(
            VerificationVerdict.CITATION_MISMATCH,
            (
                "Цитата или её координаты не совпадают с "
                "неизменяемым snapshot источника."
            ),
        )

    context_start = max(0, start - CONTEXT_CHARS)
    context_end = min(len(content), end + CONTEXT_CHARS)
    evidence_context = content[
        context_start:context_end
    ]
    source = snapshot.source

    packet = ClaimVerificationPacket(
        claim_id=str(claim.id),
        claim=claim.text,
        scope=claim.scope,
        evidence_quote=quote,
        evidence_context=evidence_context,
        source_snapshot_id=str(snapshot.id),
        source_url=(
            source.canonical_url
            if source is not None
            else snapshot.final_url
        ),
    )

    claim_numbers = set(NUMBER_PATTERN.findall(claim.text))
    context_numbers = set(
        NUMBER_PATTERN.findall(evidence_context)
    )
    missing_numbers = sorted(
        claim_numbers - context_numbers
    )

    if missing_numbers:
        return None, _result(
            VerificationVerdict.INSUFFICIENT_EVIDENCE,
            (
                "Источник не содержит заявленные в claim "
                "числовые значения: "
                + ", ".join(missing_numbers)
                + "."
            ),
        )

    return packet, None


def generate_verification_result(
    packet: ClaimVerificationPacket,
    *,
    run_id: uuid.UUID | None = None,
) -> VerificationResult:
    model = create_verifier_model()
    structured_model = model.with_structured_output(
        VerificationResult,
        method="json_schema",
        strict=True,
    )
    messages = [
        SystemMessage(
            content=load_prompt("verifier-v1.md")
        ),
        HumanMessage(
            content=(
                "Проверь утверждение по evidence packet. "
                "Пакет передан как JSON:\n\n"
                + json.dumps(
                    packet.model_dump(mode="json"),
                    ensure_ascii=False,
                )
            )
        ),
    ]

    if run_id is not None:
        consume_run_budget(
            run_id,
            external_requests=1,
            tokens=estimate_tokens(
                json.dumps(
                    packet.model_dump(mode="json"),
                    ensure_ascii=False,
                )
            ),
        )

    result = retry_external_call(
        "verifier_llm",
        structured_model.invoke,
        messages,
    )

    if not isinstance(result, VerificationResult):
        raise TypeError(
            "Verifier returned an unexpected result type"
        )

    return result


def verify_claim_record(
    claim: Claim,
    *,
    generate_fn: Callable[
        [ClaimVerificationPacket],
        VerificationResult,
    ]
    | None = None,
) -> VerificationResult:
    packet, preflight_result = (
        prepare_verification_packet(claim)
    )

    if preflight_result is not None:
        return preflight_result

    if packet is None:
        raise RuntimeError(
            "Verifier preflight produced no result or packet"
        )

    generator = generate_fn or generate_verification_result

    return generator(packet)


def verify_claim(
    claim_id: uuid.UUID,
    *,
    generate_fn: Callable[
        [ClaimVerificationPacket],
        VerificationResult,
    ]
    | None = None,
) -> VerificationResult:
    with SessionFactory() as session:
        claim = get_claim(
            session=session,
            claim_id=claim_id,
        )

        if claim is None:
            raise RuntimeError(f"Claim not found: {claim_id}")

        generator = generate_fn

        if generator is None:
            def generator(
                packet: ClaimVerificationPacket,
            ) -> VerificationResult:
                return generate_verification_result(
                    packet,
                    run_id=claim.run_id,
                )

        result = verify_claim_record(
            claim,
            generate_fn=generator,
        )
        source_id = (
            claim.source_snapshot.source_id
            if claim.source_snapshot is not None
            else None
        )

    checked_source_ids = (
        [str(source_id)]
        if source_id is not None
        else []
    )

    with SessionFactory() as session:
        create_verification(
            session=session,
            claim_id=claim_id,
            verifier_agent=VERIFIER_AGENT,
            verdict=result.verdict,
            confidence=result.confidence,
            reason=result.reason,
            checked_source_ids=checked_source_ids,
        )

    return result


def verify_claims(
    claim_ids: Iterable[str],
) -> list[dict]:
    results: list[dict] = []
    seen: set[uuid.UUID] = set()

    for raw_claim_id in claim_ids:
        claim_id = uuid.UUID(raw_claim_id)

        if claim_id in seen:
            continue

        seen.add(claim_id)

        try:
            result = verify_claim(claim_id)
        except Exception as error:
            logger.exception(
                "Claim verification failed claim_id=%s",
                claim_id,
            )
            results.append(
                {
                    "claim_id": str(claim_id),
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                }
            )
            continue

        results.append(
            {
                "claim_id": str(claim_id),
                **result.model_dump(mode="json"),
            }
        )

    return results


def verifier_task_node(
    state: VerificationWorkerState,
) -> dict:
    raw_claim_id = state["claim_id"]

    try:
        claim_id = uuid.UUID(raw_claim_id)
        result = verify_claim(claim_id)
        verification = {
            "claim_id": raw_claim_id,
            **result.model_dump(mode="json"),
        }
    except Exception as error:
        logger.exception(
            "Claim verification failed claim_id=%s",
            raw_claim_id,
        )
        user_error = classify_expected_error(error)
        error_data = (
            user_error.as_dict()
            if user_error is not None
            else {
                "code": "unexpected_verifier_error",
                "message": str(error),
                "action": (
                    "Inspect application logs for the "
                    "failed claim verification."
                ),
                "retryable": False,
            }
        )
        verification = {
            "claim_id": raw_claim_id,
            "error": error_data,
        }

    return {
        "verifications": [verification],
    }
