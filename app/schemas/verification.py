from pydantic import BaseModel, Field

from app.db.models import VerificationVerdict


class VerificationResult(BaseModel):
    """Structured decision produced for one research claim."""

    verdict: VerificationVerdict = Field(
        description=(
            "Итог проверки утверждения относительно переданного "
            "источника и точной цитаты."
        ),
    )

    confidence: float = Field(
        ge=0,
        le=1,
        description="Уверенность Verifier в итоговом решении.",
    )

    reason: str = Field(
        min_length=10,
        max_length=3000,
        description=(
            "Конкретное объяснение решения со ссылкой на "
            "утверждение, цитату и контекст."
        ),
    )


class ClaimVerificationPacket(BaseModel):
    """Immutable evidence packet passed to the Verifier model."""

    claim_id: str
    claim: str
    scope: str | None = None
    evidence_quote: str
    evidence_context: str
    source_snapshot_id: str
    source_url: str
