from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from app.db.models import (
    ReviewDecisionType,
    ReviewerRole,
)
from app.db.repositories import (
    get_claim_verifications_for_run,
    get_claims_for_run,
    get_operational_events_for_run,
    get_research_report,
    get_research_run,
    get_review_decisions_for_run,
    get_tasks_for_run,
    list_research_runs,
)
from app.db.session import SessionFactory
from app.operations import (
    export_to_obsidian,
    publish_report,
    request_additional_research,
    review_claim,
    review_report,
)
from app.config import get_settings
from app.observability import configure_structured_logging
from app.rbac import (
    authorize,
    list_reviewers,
    register_reviewer,
    set_reviewer_active,
)


def _json_default(value: Any) -> str:
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _print_json(value: Any) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "must be a valid UUID"
        ) from error


def _run_data(run) -> dict:
    return {
        "id": run.id,
        "question": run.question,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "limits": {
            "external_requests": (
                f"{run.external_requests_used}/"
                f"{run.max_external_requests}"
            ),
            "sources": run.max_sources,
            "claims": run.max_claims,
            "tokens": (
                f"{run.tokens_used}/{run.max_tokens}"
            ),
            "run_seconds": run.max_run_seconds,
        },
    }


def _task_data(task) -> dict:
    return {
        "id": task.id,
        "type": task.task_type,
        "question": task.question,
        "status": task.status,
        "priority": task.priority,
        "input": task.input_data,
        "output": task.output_data,
    }


def _claim_data(claim) -> dict:
    return {
        "id": claim.id,
        "text": claim.text,
        "status": claim.status,
        "review_status": claim.review_status,
        "evidence_quote": claim.evidence_quote,
        "source_snapshot_id": claim.source_snapshot_id,
        "research_task_id": claim.research_task_id,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect research provenance and perform "
            "human review."
        )
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    runs = subparsers.add_parser("runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    for name in (
        "run",
        "tasks",
        "claims",
        "verifications",
        "report",
        "events",
    ):
        command = subparsers.add_parser(name)
        command.add_argument("run_id", type=_uuid)
        command.add_argument(
            "--reviewer",
            default=getpass.getuser(),
        )
        if name == "events":
            command.add_argument(
                "--limit",
                type=int,
                default=200,
            )

    claim_review = subparsers.add_parser(
        "review-claim"
    )
    claim_review.add_argument(
        "decision",
        choices=("approve", "reject", "research"),
    )
    claim_review.add_argument("claim_id", type=_uuid)
    claim_review.add_argument(
        "--reason",
        required=True,
    )
    claim_review.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    report_review = subparsers.add_parser(
        "review-report"
    )
    report_review.add_argument(
        "decision",
        choices=("approve", "reject"),
    )
    report_review.add_argument("run_id", type=_uuid)
    report_review.add_argument(
        "--reason",
        required=True,
    )
    report_review.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    publish = subparsers.add_parser("publish")
    publish.add_argument("run_id", type=_uuid)
    publish.add_argument("--reason", required=True)
    publish.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    export = subparsers.add_parser(
        "export-obsidian"
    )
    export.add_argument("run_id", type=_uuid)
    export.add_argument("vault_directory", type=Path)
    export.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    rebuild = subparsers.add_parser(
        "rebuild-report"
    )
    rebuild.add_argument("run_id", type=_uuid)
    rebuild.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    reviewers = subparsers.add_parser("reviewers")
    reviewers.add_argument(
        "--reviewer",
        default=getpass.getuser(),
    )

    reviewer_add = subparsers.add_parser(
        "reviewer-add"
    )
    reviewer_add.add_argument("subject")
    reviewer_add.add_argument("display_name")
    reviewer_add.add_argument(
        "--role",
        choices=tuple(
            role.value
            for role in ReviewerRole
        ),
        required=True,
    )
    reviewer_add.add_argument("--actor")

    for command_name, active in (
        ("reviewer-enable", True),
        ("reviewer-disable", False),
    ):
        command = subparsers.add_parser(command_name)
        command.add_argument("subject")
        command.add_argument("--actor", required=True)
        command.set_defaults(reviewer_active=active)

    return parser


def _read_command(arguments: argparse.Namespace) -> None:
    with SessionFactory() as session:
        authorize(
            session,
            arguments.reviewer,
            "view",
        )

        if arguments.command == "runs":
            _print_json(
                [
                    _run_data(run)
                    for run in list_research_runs(
                        session,
                        limit=arguments.limit,
                    )
                ]
            )
            return

        run = get_research_run(
            session,
            arguments.run_id,
        )

        if run is None:
            raise RuntimeError(
                f"Research run not found: {arguments.run_id}"
            )

        if arguments.command == "tasks":
            _print_json(
                [
                    _task_data(task)
                    for task in get_tasks_for_run(
                        session,
                        run.id,
                    )
                ]
            )
            return

        if arguments.command == "claims":
            _print_json(
                [
                    _claim_data(claim)
                    for claim in get_claims_for_run(
                        session,
                        run.id,
                    )
                ]
            )
            return

        if arguments.command == "verifications":
            pairs = get_claim_verifications_for_run(
                session,
                run.id,
                verifier_agent="verifier-v1",
            )
            _print_json(
                [
                    {
                        "claim_id": claim.id,
                        "verdict": verification.verdict,
                        "confidence": (
                            verification.confidence
                        ),
                        "reason": verification.reason,
                        "verifier": (
                            verification.verifier_agent
                        ),
                    }
                    for claim, verification in pairs
                ]
            )
            return

        if arguments.command == "events":
            _print_json(
                [
                    {
                        "id": event.id,
                        "correlation_id": (
                            event.correlation_id
                        ),
                        "task_id": event.task_id,
                        "claim_id": event.claim_id,
                        "agent": event.agent,
                        "operation": event.operation,
                        "event_type": event.event_type,
                        "status": event.status,
                        "attempt": event.attempt,
                        "duration_ms": event.duration_ms,
                        "token_estimate": (
                            event.token_estimate
                        ),
                        "estimated_cost_usd": (
                            event.estimated_cost_usd
                        ),
                        "error_code": event.error_code,
                        "metadata": event.metadata_json,
                        "created_at": event.created_at,
                    }
                    for event in (
                        get_operational_events_for_run(
                            session,
                            run.id,
                            limit=arguments.limit,
                        )
                    )
                ]
            )
            return

        report = get_research_report(session, run.id)

        if arguments.command == "report":
            _print_json(
                None
                if report is None
                else {
                    "id": report.id,
                    "run_id": report.run_id,
                    "review_status": report.review_status,
                    "markdown_path": report.markdown_path,
                    "json_path": report.json_path,
                    "approved_at": report.approved_at,
                    "published_at": report.published_at,
                }
            )
            return

        _print_json(
            {
                **_run_data(run),
                "tasks": [
                    _task_data(task)
                    for task in get_tasks_for_run(
                        session,
                        run.id,
                    )
                ],
                "claims": [
                    _claim_data(claim)
                    for claim in get_claims_for_run(
                        session,
                        run.id,
                    )
                ],
                "report": (
                    None
                    if report is None
                    else {
                        "id": report.id,
                        "review_status": (
                            report.review_status
                        ),
                    }
                ),
                "review_decisions": [
                    {
                        "id": entry.id,
                        "target_type": entry.target_type,
                        "target_id": entry.target_id,
                        "decision": entry.decision,
                        "reason": entry.reason,
                        "reviewer": entry.reviewer,
                        "created_at": entry.created_at,
                    }
                    for entry in get_review_decisions_for_run(
                        session,
                        run.id,
                    )
                ],
            }
        )


def main(argv: list[str] | None = None) -> int:
    configure_structured_logging(
        get_settings().log_level
    )
    arguments = _parser().parse_args(argv)

    try:
        if arguments.command in {
            "runs",
            "run",
            "tasks",
            "claims",
            "verifications",
            "report",
            "events",
            "reviewers",
        }:
            if arguments.command == "reviewers":
                with SessionFactory() as session:
                    authorize(
                        session,
                        arguments.reviewer,
                        "view",
                    )
                    _print_json(
                        [
                            {
                                "subject": item.subject,
                                "display_name": (
                                    item.display_name
                                ),
                                "role": item.role,
                                "active": item.active,
                            }
                            for item in list_reviewers(
                                session
                            )
                        ]
                    )
            else:
                _read_command(arguments)
        elif arguments.command == "review-claim":
            if arguments.decision == "research":
                task = request_additional_research(
                    arguments.claim_id,
                    reason=arguments.reason,
                    reviewer=arguments.reviewer,
                )
                _print_json(
                    {
                        "status": "research_requested",
                        "task_id": task.id,
                        "run_id": task.run_id,
                    }
                )
            else:
                decision = (
                    ReviewDecisionType.APPROVE
                    if arguments.decision == "approve"
                    else ReviewDecisionType.REJECT
                )
                run_id = review_claim(
                    arguments.claim_id,
                    decision=decision,
                    reason=arguments.reason,
                    reviewer=arguments.reviewer,
                )
                _print_json(
                    {
                        "status": arguments.decision,
                        "run_id": run_id,
                    }
                )
        elif arguments.command == "review-report":
            decision = (
                ReviewDecisionType.APPROVE
                if arguments.decision == "approve"
                else ReviewDecisionType.REJECT
            )
            review_report(
                arguments.run_id,
                decision=decision,
                reason=arguments.reason,
                reviewer=arguments.reviewer,
            )
            _print_json({"status": arguments.decision})
        elif arguments.command == "publish":
            markdown_path, json_path = publish_report(
                arguments.run_id,
                reason=arguments.reason,
                reviewer=arguments.reviewer,
            )
            _print_json(
                {
                    "status": "published",
                    "markdown_path": markdown_path,
                    "json_path": json_path,
                }
            )
        elif arguments.command == "export-obsidian":
            note_path, json_path = export_to_obsidian(
                arguments.run_id,
                arguments.vault_directory,
                reviewer=arguments.reviewer,
            )
            _print_json(
                {
                    "status": "exported",
                    "note_path": note_path,
                    "json_path": json_path,
                }
            )
        elif arguments.command == "rebuild-report":
            with SessionFactory() as session:
                authorize(
                    session,
                    arguments.reviewer,
                    "review_report",
                )

            from app.agents.writer import writer_node

            result = writer_node(
                {"run_id": str(arguments.run_id)}
            )
            _print_json(
                {
                    "status": "rebuilt",
                    "report": result["report_json"],
                }
            )
        elif arguments.command == "reviewer-add":
            with SessionFactory() as session:
                reviewer = register_reviewer(
                    session,
                    subject=arguments.subject,
                    display_name=arguments.display_name,
                    role=ReviewerRole(arguments.role),
                    actor=arguments.actor,
                )
            _print_json(
                {
                    "subject": reviewer.subject,
                    "role": reviewer.role,
                    "active": reviewer.active,
                }
            )
        elif arguments.command in {
            "reviewer-enable",
            "reviewer-disable",
        }:
            with SessionFactory() as session:
                reviewer = set_reviewer_active(
                    session,
                    subject=arguments.subject,
                    active=arguments.reviewer_active,
                    actor=arguments.actor,
                )
            _print_json(
                {
                    "subject": reviewer.subject,
                    "active": reviewer.active,
                }
            )
    except (
        OSError,
        PermissionError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
