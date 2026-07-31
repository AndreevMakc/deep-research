from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.models import (
    EventStatus,
    OperationalEvent,
    ResearchRun,
    RunStatus,
)
from app.db.session import SessionFactory, engine
from app.observability import configure_structured_logging
from app.source_store import RUNS_DIRECTORY


REQUIRED_TABLES = {
    "research_runs",
    "research_tasks",
    "sources",
    "source_snapshots",
    "claims",
    "verifications",
    "research_reports",
    "research_report_versions",
    "claim_recheck_requests",
    "review_decisions",
    "reviewer_identities",
    "operational_events",
    "tenants",
    "api_identities",
    "browser_sessions",
    "research_run_views",
    "research_drafts",
    "work_items",
    "idempotency_records",
    "webhook_subscriptions",
    "webhook_deliveries",
}


def _percentile(
    values: list[float],
    percentile: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            math.ceil(percentile * len(ordered)) - 1,
        ),
    )
    return ordered[index]


def readiness() -> dict:
    checks: dict[str, dict] = {}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = {"ready": True}
    except SQLAlchemyError as error:
        checks["database"] = {
            "ready": False,
            "error": type(error).__name__,
        }

    try:
        tables = set(inspect(engine).get_table_names())
        missing = sorted(REQUIRED_TABLES - tables)
        checks["schema"] = {
            "ready": not missing,
            "missing_tables": missing,
        }
    except SQLAlchemyError as error:
        checks["schema"] = {
            "ready": False,
            "error": type(error).__name__,
        }

    try:
        RUNS_DIRECTORY.mkdir(parents=True, exist_ok=True)
        probe = RUNS_DIRECTORY / ".readiness"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["artifacts"] = {"ready": True}
    except OSError as error:
        checks["artifacts"] = {
            "ready": False,
            "error": type(error).__name__,
        }

    return {
        "status": (
            "ready"
            if all(
                check["ready"]
                for check in checks.values()
            )
            else "not_ready"
        ),
        "checks": checks,
    }


def collect_metrics(
    *,
    window_minutes: int = 60,
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(
        minutes=max(1, window_minutes)
    )

    with SessionFactory() as session:
        events = list(
            session.scalars(
                select(OperationalEvent).where(
                    OperationalEvent.created_at >= since
                )
            ).all()
        )
        runs = list(
            session.scalars(
                select(ResearchRun).where(
                    ResearchRun.updated_at >= since
                )
            ).all()
        )

    terminal_runs = [
        run
        for run in runs
        if run.status
        in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ERRORS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ]
    successful_runs = [
        run
        for run in terminal_runs
        if run.status
        in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_ERRORS,
        }
    ]
    external_attempts = [
        event
        for event in events
        if event.event_type == "external_call"
        and event.status
        in {
            EventStatus.SUCCEEDED,
            EventStatus.FAILED,
        }
    ]
    retries = [
        event
        for event in events
        if event.status == EventStatus.RETRYING
    ]
    external_latencies = [
        event.duration_ms
        for event in external_attempts
        if event.duration_ms is not None
    ]
    node_latencies = [
        event.duration_ms
        for event in events
        if event.event_type == "node_execution"
        and event.status == EventStatus.SUCCEEDED
        and event.duration_ms is not None
    ]
    error_counts: dict[str, int] = {}

    for event in events:
        if event.status != EventStatus.FAILED:
            continue
        code = event.error_code or "unknown"
        error_counts[code] = error_counts.get(code, 0) + 1

    return {
        "window_minutes": max(1, window_minutes),
        "runs": {
            "terminal": len(terminal_runs),
            "successful": len(successful_runs),
            "failed": (
                sum(
                    run.status == RunStatus.FAILED
                    for run in terminal_runs
                )
            ),
            "cancelled": sum(
                run.status == RunStatus.CANCELLED
                for run in terminal_runs
            ),
            "success_rate": (
                len(successful_runs) / len(terminal_runs)
                if terminal_runs
                else 1.0
            ),
        },
        "external_calls": {
            "attempts": len(external_attempts),
            "retries": len(retries),
            "retry_rate": (
                len(retries) / len(external_attempts)
                if external_attempts
                else 0.0
            ),
            "p95_duration_ms": _percentile(
                external_latencies,
                0.95,
            ),
        },
        "nodes": {
            "p95_duration_ms": _percentile(
                node_latencies,
                0.95,
            ),
        },
        "usage": {
            "token_estimate": sum(
                event.token_estimate
                for event in events
            ),
            "estimated_cost_usd": round(
                sum(
                    event.estimated_cost_usd
                    for event in events
                ),
                8,
            ),
        },
        "errors": error_counts,
    }


def evaluate_alerts(metrics: dict) -> list[dict]:
    settings = get_settings()
    alerts = []
    success_rate = metrics["runs"]["success_rate"]
    external_p95 = metrics[
        "external_calls"
    ]["p95_duration_ms"]
    retry_rate = metrics["external_calls"]["retry_rate"]

    if success_rate < settings.slo_min_run_success_rate:
        alerts.append(
            {
                "code": "run_success_rate_below_slo",
                "actual": success_rate,
                "threshold": (
                    settings.slo_min_run_success_rate
                ),
            }
        )

    if external_p95 > settings.slo_max_external_p95_ms:
        alerts.append(
            {
                "code": "external_latency_above_slo",
                "actual": external_p95,
                "threshold": (
                    settings.slo_max_external_p95_ms
                ),
            }
        )

    if retry_rate > settings.slo_max_retry_rate:
        alerts.append(
            {
                "code": "retry_rate_above_slo",
                "actual": retry_rate,
                "threshold": settings.slo_max_retry_rate,
            }
        )

    return alerts


def main(argv: list[str] | None = None) -> int:
    configure_structured_logging(
        get_settings().log_level
    )
    parser = argparse.ArgumentParser(
        description="Health, SLO metrics, and alert checks."
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )
    subparsers.add_parser("live")
    subparsers.add_parser("ready")

    for name in ("metrics", "alerts"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--window-minutes",
            type=int,
            default=60,
        )

    arguments = parser.parse_args(argv)

    if arguments.command == "live":
        result = {"status": "alive"}
        exit_code = 0
    elif arguments.command == "ready":
        result = readiness()
        exit_code = (
            0 if result["status"] == "ready" else 1
        )
    else:
        metrics = collect_metrics(
            window_minutes=arguments.window_minutes
        )

        if arguments.command == "metrics":
            result = metrics
            exit_code = 0
        else:
            alerts = evaluate_alerts(metrics)
            result = {
                "status": (
                    "ok" if not alerts else "alerting"
                ),
                "alerts": alerts,
                "metrics": metrics,
            }
            exit_code = 0 if not alerts else 1

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
