import unittest
from unittest.mock import patch

from app.health import _percentile, evaluate_alerts


class HealthTests(unittest.TestCase):
    def test_percentile(self) -> None:
        self.assertEqual(_percentile([], 0.95), 0)
        self.assertEqual(
            _percentile([1, 2, 3, 4, 5], 0.95),
            5,
        )

    @patch("app.health.get_settings")
    def test_alerts_detect_slo_violations(
        self,
        settings,
    ) -> None:
        settings.return_value.slo_min_run_success_rate = 0.95
        settings.return_value.slo_max_external_p95_ms = 100
        settings.return_value.slo_max_retry_rate = 0.1
        alerts = evaluate_alerts(
            {
                "runs": {"success_rate": 0.5},
                "external_calls": {
                    "p95_duration_ms": 200,
                    "retry_rate": 0.2,
                },
            }
        )
        self.assertEqual(len(alerts), 3)

    @patch("app.health.get_settings")
    def test_alerts_pass_healthy_metrics(
        self,
        settings,
    ) -> None:
        settings.return_value.slo_min_run_success_rate = 0.95
        settings.return_value.slo_max_external_p95_ms = 100
        settings.return_value.slo_max_retry_rate = 0.1
        alerts = evaluate_alerts(
            {
                "runs": {"success_rate": 1.0},
                "external_calls": {
                    "p95_duration_ms": 50,
                    "retry_rate": 0,
                },
            }
        )
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
