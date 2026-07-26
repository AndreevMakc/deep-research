from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.agents.verifier import verify_claim_record
from app.agents.writer import validate_writer_draft
from app.db.models import (
    Claim,
    Source,
    SourceSnapshot,
    VerificationVerdict,
)
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationMetrics,
    EvaluationReport,
    EvaluationThresholds,
)
from app.schemas.verification import VerificationResult
from app.schemas.writer import (
    CitedStatement,
    WriterClaimEvidence,
    WriterDraft,
    WriterPacket,
)
from app.state import (
    merge_findings,
    merge_unique,
    merge_verifications,
)
from app.tools.source_fetch import canonicalize_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "cases.json"
DEFAULT_THRESHOLDS = (
    PROJECT_ROOT / "evals" / "thresholds.json"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "data" / "evaluations"
)
ACCEPTED_VERDICTS = {
    VerificationVerdict.SUPPORTED,
    VerificationVerdict.PARTIALLY_SUPPORTED,
}


def load_dataset(
    path: Path = DEFAULT_DATASET,
) -> tuple[EvaluationDataset, str]:
    raw = path.read_bytes()
    dataset = EvaluationDataset.model_validate_json(
        raw
    )

    return (
        dataset,
        hashlib.sha256(raw).hexdigest(),
    )


def load_thresholds(
    path: Path = DEFAULT_THRESHOLDS,
) -> EvaluationThresholds:
    return EvaluationThresholds.model_validate_json(
        path.read_bytes()
    )


def _make_claim(
    case: EvaluationCase,
    directory: Path,
) -> Claim:
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, case.id)
    source_id = uuid.uuid5(run_id, "source")
    snapshot_id = uuid.uuid5(run_id, "snapshot")
    claim_id = uuid.uuid5(run_id, "claim")
    source_path = directory / f"{case.id}.txt"

    if case.source_available:
        source_path.write_text(
            case.source_content,
            encoding="utf-8",
        )

    source = Source(
        id=source_id,
        run_id=run_id,
        url=case.source_url,
        canonical_url=case.source_url,
        title=f"Evaluation source {case.id}",
    )
    snapshot = SourceSnapshot(
        id=snapshot_id,
        source_id=source_id,
        run_id=run_id,
        final_url=case.source_url,
        content_hash=hashlib.sha256(
            case.source_content.encode("utf-8")
        ).hexdigest(),
        mime_type="text/plain",
        local_path=str(source_path),
        content_length=len(
            case.source_content.encode("utf-8")
        ),
        metadata_json={"evaluation_case": case.id},
    )
    snapshot.source = source
    quote_start = case.source_content.find(
        case.evidence_quote
    )

    if quote_start < 0:
        quote_start = 0

    claim = Claim(
        id=claim_id,
        run_id=run_id,
        source_snapshot_id=snapshot_id,
        text=case.claim_text,
        evidence_quote=case.evidence_quote,
        quote_start=quote_start,
        quote_end=quote_start + len(
            case.evidence_quote
        ),
        locator={"evaluation_case": case.id},
        scope=f"Evaluation case {case.id}",
        created_by_agent="evaluation-fixture",
    )
    claim.source_snapshot = snapshot

    return claim


def _semantic_result(
    case: EvaluationCase,
) -> VerificationResult:
    verdict = (
        case.semantic_verdict
        or VerificationVerdict.INSUFFICIENT_EVIDENCE
    )

    return VerificationResult(
        verdict=verdict,
        confidence=0.95,
        reason=(
            "Offline semantic oracle result for "
            f"evaluation case {case.id}."
        ),
    )


def _evaluate_writer_statement(
    case: EvaluationCase,
    actual_verdict: VerificationVerdict | None,
    claim: Claim,
) -> tuple[bool | None, str | None]:
    if case.expected_report_valid is None:
        return None, None

    if not case.report_statement:
        return False, "Report statement is missing"

    if not case.report_claim_ids:
        return False, "Report statement has no citation"

    claim_id = case.id
    evidence = WriterClaimEvidence(
        claim_id=claim_id,
        statement=case.claim_text,
        evidence_quote=case.evidence_quote,
        scope=claim.scope,
        verdict=(
            actual_verdict
            or VerificationVerdict.INSUFFICIENT_EVIDENCE
        ),
        confidence=0.95,
        verification_reason=(
            "Offline evaluation verification."
        ),
        source_snapshot_id=str(
            claim.source_snapshot_id
        ),
        source_url=case.source_url,
        source_title=f"Evaluation source {case.id}",
    )
    accepted = (
        [evidence]
        if actual_verdict in ACCEPTED_VERDICTS
        else []
    )
    rejected = (
        []
        if actual_verdict in ACCEPTED_VERDICTS
        else [evidence]
    )
    packet = WriterPacket(
        run_id=f"evaluation-{case.id}",
        question=case.question,
        accepted_claims=accepted,
        rejected_claims=rejected,
    )
    translated_ids = [
        claim_id if value == "self" else value
        for value in case.report_claim_ids
    ]

    try:
        statement = CitedStatement(
            text=case.report_statement,
            claim_ids=translated_ids,
            qualification=case.report_qualification,
        )
        draft = WriterDraft(
            short_answer=[statement],
        )
        validate_writer_draft(draft, packet)
    except (ValueError, KeyError) as error:
        return False, str(error)

    return True, None


def _evaluate_case(
    case: EvaluationCase,
    directory: Path,
) -> dict:
    try:
        canonical_url = canonicalize_url(
            case.source_url
        )
        actual_url_valid = True
    except (TypeError, ValueError):
        canonical_url = None
        actual_url_valid = False

    claim = _make_claim(case, directory)
    exact_quote = (
        case.source_available
        and case.evidence_quote
        in case.source_content
    )
    actual_verdict: VerificationVerdict | None = None

    if actual_url_valid:
        result = verify_claim_record(
            claim,
            generate_fn=lambda _packet: (
                _semantic_result(case)
            ),
        )
        actual_verdict = result.verdict

    report_valid, report_error = (
        _evaluate_writer_statement(
            case,
            actual_verdict,
            claim,
        )
    )
    url_matches = (
        actual_url_valid
        == case.expected_url_valid
    )
    verdict_matches = (
        actual_verdict
        == case.expected_verdict
    )
    report_matches = (
        report_valid
        == case.expected_report_valid
        if case.expected_report_valid is not None
        else True
    )

    return {
        "id": case.id,
        "negative_fixture": case.negative_fixture,
        "expected_url_valid": case.expected_url_valid,
        "actual_url_valid": actual_url_valid,
        "canonical_url": canonical_url,
        "exact_quote": exact_quote,
        "expected_verdict": (
            case.expected_verdict.value
            if case.expected_verdict is not None
            else None
        ),
        "actual_verdict": (
            actual_verdict.value
            if actual_verdict is not None
            else None
        ),
        "expected_report_valid": (
            case.expected_report_valid
        ),
        "actual_report_valid": report_valid,
        "report_error": report_error,
        "passed": (
            url_matches
            and verdict_matches
            and report_matches
        ),
    }


def _recovery_success_rate() -> float:
    findings = merge_findings(
        [
            {
                "task_id": "task-1",
                "result": {"error": "temporary"},
            }
        ],
        [
            {
                "task_id": "task-1",
                "result": {"summary": "recovered"},
            }
        ],
    )
    verifications = merge_verifications(
        [
            {
                "claim_id": "claim-1",
                "error": "temporary",
            }
        ],
        [
            {
                "claim_id": "claim-1",
                "verdict": "supported",
            }
        ],
    )
    claim_ids = merge_unique(
        ["claim-1"],
        ["claim-1"],
    )
    recovered = (
        findings[0]["result"].get("summary")
        == "recovered"
        and verifications[0].get("verdict")
        == "supported"
        and claim_ids == ["claim-1"]
    )

    return 1.0 if recovered else 0.0


def _safe_rate(
    numerator: int,
    denominator: int,
) -> float:
    return (
        numerator / denominator
        if denominator
        else 1.0
    )


def _calculate_metrics(
    cases: list[dict],
    duration_seconds: float,
) -> EvaluationMetrics:
    verdict_cases = [
        case
        for case in cases
        if case["expected_verdict"] is not None
    ]
    actual_verdicts = [
        case["actual_verdict"]
        for case in verdict_cases
        if case["actual_verdict"] is not None
    ]
    valid_report_cases = [
        case
        for case in cases
        if case["expected_report_valid"] is True
    ]
    quote_cases = [
        case
        for case in cases
        if case["actual_url_valid"]
    ]
    negative_cases = [
        case
        for case in cases
        if case["negative_fixture"]
    ]
    canonical_urls = [
        case["canonical_url"]
        for case in cases
        if case["canonical_url"] is not None
    ]
    duplicate_source_count = (
        len(canonical_urls)
        - len(set(canonical_urls))
    )
    supported = {
        VerificationVerdict.SUPPORTED.value,
        VerificationVerdict.PARTIALLY_SUPPORTED.value,
    }

    return EvaluationMetrics(
        total_cases=len(cases),
        verdict_accuracy=round(
            _safe_rate(
                sum(
                    case["actual_verdict"]
                    == case["expected_verdict"]
                    for case in verdict_cases
                ),
                len(verdict_cases),
            ),
            4,
        ),
        citation_coverage=round(
            _safe_rate(
                sum(
                    case["actual_report_valid"] is True
                    for case in valid_report_cases
                ),
                len(valid_report_cases),
            ),
            4,
        ),
        citation_mismatch_rate=round(
            _safe_rate(
                actual_verdicts.count(
                    VerificationVerdict
                    .CITATION_MISMATCH.value
                ),
                len(actual_verdicts),
            ),
            4,
        ),
        supported_claim_rate=round(
            _safe_rate(
                sum(
                    verdict in supported
                    for verdict in actual_verdicts
                ),
                len(actual_verdicts),
            ),
            4,
        ),
        exact_quote_rate=round(
            _safe_rate(
                sum(
                    case["exact_quote"]
                    for case in quote_cases
                ),
                len(quote_cases),
            ),
            4,
        ),
        invalid_fixture_detection_rate=round(
            _safe_rate(
                sum(
                    case["passed"]
                    for case in negative_cases
                ),
                len(negative_cases),
            ),
            4,
        ),
        recovery_success_rate=(
            _recovery_success_rate()
        ),
        duplicate_source_count=duplicate_source_count,
        external_request_count=0,
        estimated_external_cost_usd=0.0,
        duration_seconds=round(
            duration_seconds,
            6,
        ),
    )


def _threshold_failures(
    metrics: EvaluationMetrics,
    thresholds: EvaluationThresholds,
) -> list[str]:
    failures = []
    minimums = {
        "verdict_accuracy": thresholds.verdict_accuracy,
        "citation_coverage": thresholds.citation_coverage,
        "invalid_fixture_detection_rate": (
            thresholds.invalid_fixture_detection_rate
        ),
        "recovery_success_rate": (
            thresholds.recovery_success_rate
        ),
    }

    for name, minimum in minimums.items():
        value = getattr(metrics, name)

        if value < minimum:
            failures.append(
                f"{name}={value:.4f} < {minimum:.4f}"
            )

    if (
        metrics.external_request_count
        > thresholds.max_external_request_count
    ):
        failures.append(
            "external_request_count="
            f"{metrics.external_request_count} > "
            f"{thresholds.max_external_request_count}"
        )

    return failures


def _comparison(
    metrics: EvaluationMetrics,
    baseline_path: Path | None,
) -> dict[str, float]:
    if baseline_path is None:
        return {}

    baseline = EvaluationReport.model_validate_json(
        baseline_path.read_bytes()
    )
    current = metrics.model_dump()
    previous = baseline.metrics.model_dump()

    return {
        name: round(
            float(current[name]) - float(previous[name]),
            6,
        )
        for name in current
        if isinstance(current[name], (int, float))
    }


def run_evaluation(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    thresholds_path: Path = DEFAULT_THRESHOLDS,
    baseline_path: Path | None = None,
) -> EvaluationReport:
    dataset, dataset_hash = load_dataset(dataset_path)
    thresholds = load_thresholds(thresholds_path)
    started = time.perf_counter()

    with tempfile.TemporaryDirectory() as directory:
        cases = [
            _evaluate_case(case, Path(directory))
            for case in dataset.cases
        ]

    duration = time.perf_counter() - started
    metrics = _calculate_metrics(cases, duration)
    failures = _threshold_failures(
        metrics,
        thresholds,
    )

    return EvaluationReport(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        dataset_hash=dataset_hash,
        created_at=datetime.now(UTC).isoformat(),
        metrics=metrics,
        thresholds=thresholds,
        threshold_failures=failures,
        passed=not failures,
        cases=cases,
        comparison=_comparison(
            metrics,
            baseline_path,
        ),
    )


def render_evaluation_markdown(
    report: EvaluationReport,
) -> str:
    lines = [
        "# Deep Research Evaluation",
        "",
        f"- Dataset: `{report.dataset_name}`",
        f"- Version: `{report.dataset_version}`",
        f"- Passed: **{report.passed}**",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    for name, value in report.metrics.model_dump().items():
        lines.append(f"| `{name}` | {value} |")

    if report.threshold_failures:
        lines.extend(
            [
                "",
                "## Threshold failures",
                "",
                *[
                    f"- {failure}"
                    for failure in report.threshold_failures
                ],
            ]
        )

    if report.comparison:
        lines.extend(
            [
                "",
                "## Baseline delta",
                "",
                "| Metric | Delta |",
                "|---|---:|",
                *[
                    f"| `{name}` | {value:+.6f} |"
                    for name, value
                    in report.comparison.items()
                ],
            ]
        )

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Expected | Actual | Passed |",
            "|---|---|---|---:|",
        ]
    )

    for case in report.cases:
        lines.append(
            f"| `{case['id']}` | "
            f"{case['expected_verdict']} | "
            f"{case['actual_verdict']} | "
            f"{case['passed']} |"
        )

    return "\n".join(lines).rstrip() + "\n"


def write_evaluation_report(
    report: EvaluationReport,
    output_directory: Path,
) -> tuple[Path, Path]:
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    json_path = output_directory / "evaluation.json"
    markdown_path = output_directory / "evaluation.md"
    json_path.write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_evaluation_markdown(report),
        encoding="utf-8",
    )

    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the offline deep-research quality baseline."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=DEFAULT_THRESHOLDS,
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    args = parser.parse_args()
    report = run_evaluation(
        dataset_path=args.dataset,
        thresholds_path=args.thresholds,
        baseline_path=args.baseline,
    )
    run_directory = (
        args.output_root
        / datetime.now(UTC).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
    )
    json_path, markdown_path = (
        write_evaluation_report(
            report,
            run_directory,
        )
    )
    print(f"Evaluation JSON: {json_path}")
    print(f"Evaluation Markdown: {markdown_path}")
    print(f"Passed: {report.passed}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
