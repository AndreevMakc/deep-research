import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.evaluation import (
    _threshold_failures,
    load_dataset,
    run_evaluation,
    write_evaluation_report,
)
from app.schemas.evaluation import EvaluationThresholds


class EvaluationTests(unittest.TestCase):
    def test_offline_baseline_passes_without_models(
        self,
    ) -> None:
        with (
            patch(
                "app.agents.verifier."
                "create_worker_model"
            ) as verifier_model,
            patch(
                "app.agents.writer.create_writer_model"
            ) as writer_model,
        ):
            report = run_evaluation()

        verifier_model.assert_not_called()
        writer_model.assert_not_called()
        self.assertTrue(report.passed)
        self.assertEqual(
            report.metrics.total_cases,
            14,
        )
        self.assertEqual(
            report.metrics.external_request_count,
            0,
        )
        self.assertEqual(
            report.metrics.verdict_accuracy,
            1.0,
        )
        self.assertEqual(
            report.metrics.citation_coverage,
            1.0,
        )
        self.assertEqual(
            report.metrics.invalid_fixture_detection_rate,
            1.0,
        )
        self.assertEqual(
            report.metrics.duplicate_source_count,
            1,
        )

    def test_threshold_failure_is_reported(
        self,
    ) -> None:
        report = run_evaluation()
        degraded = report.metrics.model_copy(
            update={"verdict_accuracy": 0.5}
        )
        failures = _threshold_failures(
            degraded,
            EvaluationThresholds(),
        )

        self.assertTrue(
            any(
                failure.startswith(
                    "verdict_accuracy="
                )
                for failure in failures
            )
        )

    def test_writes_reports_and_compares_baseline(
        self,
    ) -> None:
        baseline = run_evaluation()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_json, _ = write_evaluation_report(
                baseline,
                root / "baseline",
            )
            compared = run_evaluation(
                baseline_path=baseline_json,
            )
            json_path, markdown_path = (
                write_evaluation_report(
                    compared,
                    root / "current",
                )
            )

            self.assertTrue(json_path.is_file())
            self.assertTrue(markdown_path.is_file())
            self.assertEqual(
                compared.comparison[
                    "verdict_accuracy"
                ],
                0.0,
            )
            self.assertIn(
                "Baseline delta",
                markdown_path.read_text(
                    encoding="utf-8"
                ),
            )

    def test_dataset_has_unique_control_cases(
        self,
    ) -> None:
        dataset, dataset_hash = load_dataset()
        case_ids = [
            case.id
            for case in dataset.cases
        ]

        self.assertGreaterEqual(len(case_ids), 10)
        self.assertEqual(
            len(case_ids),
            len(set(case_ids)),
        )
        self.assertEqual(len(dataset_hash), 64)


if __name__ == "__main__":
    unittest.main()
