from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import VerificationVerdict


class WriterClaimEvidence(BaseModel):
    """One verified claim available to the Writer."""

    claim_id: str
    statement: str
    evidence_quote: str | None = None
    scope: str | None = None
    verdict: VerificationVerdict
    confidence: float = Field(ge=0, le=1)
    verification_reason: str
    source_snapshot_id: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    source_publisher: str | None = None
    source_published_at: datetime | None = None
    source_retrieved_at: datetime | None = None


class WriterPacket(BaseModel):
    """Complete provenance packet for final synthesis."""

    run_id: str
    question: str
    accepted_claims: list[WriterClaimEvidence]
    rejected_claims: list[WriterClaimEvidence]
    known_unanswered_questions: list[str] = Field(
        default_factory=list,
    )


class CitedStatement(BaseModel):
    """A report statement backed by explicit claim IDs."""

    text: str = Field(
        min_length=5,
        max_length=3000,
    )
    claim_ids: list[str] = Field(
        min_length=1,
        max_length=8,
    )
    qualification: str | None = Field(
        default=None,
        max_length=2000,
    )


class ReportSection(BaseModel):
    heading: str = Field(
        min_length=3,
        max_length=200,
    )
    statements: list[CitedStatement] = Field(
        min_length=1,
        max_length=20,
    )


class ReportFinding(BaseModel):
    """One scannable key finding in the editorial report."""

    title: str = Field(
        min_length=3,
        max_length=200,
    )
    statement: CitedStatement


class WriterDraft(BaseModel):
    """Schema-constrained output returned by the Writer LLM."""

    direct_answer: CitedStatement | None = None
    key_findings: list[ReportFinding] = Field(
        default_factory=list,
        max_length=8,
    )
    short_answer: list[CitedStatement] = Field(
        default_factory=list,
        max_length=5,
    )
    sections: list[ReportSection] = Field(
        default_factory=list,
        max_length=12,
    )
    limitations: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    contradictions: list[CitedStatement] = Field(
        default_factory=list,
        max_length=10,
    )
    unanswered_questions: list[str] = Field(
        default_factory=list,
        max_length=20,
    )


class ReportSource(BaseModel):
    citation_label: str
    claim_id: str
    source_snapshot_id: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    source_publisher: str | None = None
    source_published_at: datetime | None = None
    source_retrieved_at: datetime | None = None
    evidence_quote: str | None = None
    verdict: VerificationVerdict
    confidence: float = Field(ge=0, le=1)
    verification_reason: str


class EvidenceQualitySummary(BaseModel):
    """Aggregate trust signals shown before the long report."""

    confirmed_claims: int = Field(ge=0)
    limited_claims: int = Field(ge=0)
    contradicted_claims: int = Field(ge=0)
    unsupported_claims: int = Field(ge=0)
    source_count: int = Field(ge=0)
    overall_confidence: float = Field(ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)


class FinalResearchReport(BaseModel):
    """Validated report used for both JSON and Markdown."""

    run_id: str
    question: str
    direct_answer: CitedStatement | None = None
    key_findings: list[ReportFinding] = Field(
        default_factory=list,
    )
    short_answer: list[CitedStatement]
    sections: list[ReportSection]
    limitations: list[str]
    contradictions: list[CitedStatement]
    unanswered_questions: list[str]
    sources: list[ReportSource]
    overall_confidence: float = Field(ge=0, le=1)
    quality_summary: EvidenceQualitySummary
