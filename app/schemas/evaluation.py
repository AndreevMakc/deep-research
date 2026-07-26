from pydantic import BaseModel, Field, model_validator

from app.db.models import VerificationVerdict


class EvaluationCase(BaseModel):
    id: str = Field(min_length=3, max_length=100)
    question: str = Field(min_length=10)
    source_url: str
    source_content: str = Field(min_length=1)
    source_available: bool = True
    claim_text: str = Field(min_length=5)
    evidence_quote: str = Field(min_length=1)
    semantic_verdict: VerificationVerdict | None = None
    expected_verdict: VerificationVerdict | None = None
    expected_url_valid: bool = True
    report_statement: str | None = None
    report_claim_ids: list[str] = Field(
        default_factory=list,
    )
    report_qualification: str | None = None
    expected_report_valid: bool | None = None
    negative_fixture: bool = False


class EvaluationDataset(BaseModel):
    name: str
    version: str
    cases: list[EvaluationCase] = Field(
        min_length=10,
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_unique_case_ids(self):
        case_ids = [
            case.id
            for case in self.cases
        ]

        if len(case_ids) != len(set(case_ids)):
            raise ValueError(
                "Evaluation case IDs must be unique"
            )

        return self


class EvaluationMetrics(BaseModel):
    total_cases: int
    verdict_accuracy: float = Field(ge=0, le=1)
    citation_coverage: float = Field(ge=0, le=1)
    citation_mismatch_rate: float = Field(ge=0, le=1)
    supported_claim_rate: float = Field(ge=0, le=1)
    exact_quote_rate: float = Field(ge=0, le=1)
    invalid_fixture_detection_rate: float = Field(
        ge=0,
        le=1,
    )
    recovery_success_rate: float = Field(ge=0, le=1)
    duplicate_source_count: int
    external_request_count: int
    estimated_external_cost_usd: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)


class EvaluationThresholds(BaseModel):
    verdict_accuracy: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    citation_coverage: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    invalid_fixture_detection_rate: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    recovery_success_rate: float = Field(
        default=1.0,
        ge=0,
        le=1,
    )
    max_external_request_count: int = Field(
        default=0,
        ge=0,
    )


class EvaluationReport(BaseModel):
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    created_at: str
    metrics: EvaluationMetrics
    thresholds: EvaluationThresholds
    threshold_failures: list[str]
    passed: bool
    cases: list[dict]
    comparison: dict[str, float] = Field(
        default_factory=dict,
    )
