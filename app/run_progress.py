from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    EventStatus,
    OperationalEvent,
    ResearchReport,
    ResearchRun,
    ResearchTask,
    RunStatus,
    Source,
    TaskStatus,
    Verification,
    WorkItem,
    WorkStatus,
)


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.COMPLETED_WITH_ERRORS,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


def _seconds_between(
    started_at: datetime | None,
    ended_at: datetime,
) -> int:
    if started_at is None:
        return 0

    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    return max(0, int((ended_at - started_at).total_seconds()))


def build_run_progress(
    session: Session,
    *,
    run: ResearchRun,
    include_technical: bool = False,
) -> dict:
    task_counts = dict(
        session.execute(
            select(
                ResearchTask.status,
                func.count(ResearchTask.id),
            )
            .where(ResearchTask.run_id == run.id)
            .group_by(ResearchTask.status)
        ).all()
    )
    directions_total = sum(task_counts.values())
    directions_completed = task_counts.get(
        TaskStatus.COMPLETED,
        0,
    )
    directions_failed = task_counts.get(
        TaskStatus.FAILED,
        0,
    )
    sources = session.scalar(
        select(func.count(Source.id)).where(
            Source.run_id == run.id
        )
    ) or 0
    claims = len(run.claims)
    verifications = session.scalar(
        select(func.count(Verification.id))
        .join(Verification.claim)
        .where(Verification.claim.has(run_id=run.id))
    ) or 0
    has_report = (
        session.scalar(
            select(ResearchReport.id).where(
                ResearchReport.run_id == run.id
            )
        )
        is not None
    )
    item = session.scalar(
        select(WorkItem).where(
            WorkItem.run_id == run.id,
            WorkItem.kind == "execute_research_run",
        )
    )

    finalizing = bool(
        item
        and (
            item.finish_requested
            or item.payload.get("finish_early")
        )
    )

    if run.status in TERMINAL_RUN_STATUSES:
        stage = "complete"
        activity = {
            RunStatus.COMPLETED: "Исследование завершено.",
            RunStatus.COMPLETED_WITH_ERRORS: (
                "Исследование завершено с ограничениями."
            ),
            RunStatus.FAILED: (
                "Исследование остановилось из-за ошибки."
            ),
            RunStatus.CANCELLED: "Исследование остановлено.",
        }[run.status]
    elif run.status == RunStatus.PAUSED:
        stage = "paused"
        activity = (
            "Работа на паузе. Подтверждённые результаты сохранены."
        )
    elif run.status == RunStatus.PAUSE_REQUESTED:
        stage = "pausing"
        activity = (
            "Завершаем текущую операцию и сохраняем checkpoint."
        )
    elif finalizing:
        stage = "report"
        activity = "Собираем отчёт из уже сохранённых данных."
    elif directions_total == 0:
        stage = "directions"
        activity = "Формируем направления исследования."
    elif directions_completed + directions_failed < directions_total:
        stage = "sources"
        activity = (
            f"Исследованы направления: "
            f"{directions_completed} из {directions_total}; "
            f"сохранено источников: {sources}."
        )
    elif verifications < claims:
        stage = "verification"
        activity = (
            f"Проверены выводы: {verifications} из {claims}."
        )
    else:
        stage = "report"
        activity = (
            "Собираем итоговый отчёт."
            if not has_report
            else "Итоговый отчёт сохранён."
        )

    now = datetime.now(timezone.utc)
    elapsed_until = (
        run.updated_at
        if run.status in TERMINAL_RUN_STATUSES
        else now
    )

    if elapsed_until.tzinfo is None:
        elapsed_until = elapsed_until.replace(
            tzinfo=timezone.utc
        )

    elapsed_seconds = _seconds_between(
        run.started_at,
        elapsed_until,
    )
    remaining_upper_bound = (
        None
        if (
            run.started_at is None
            or run.status
            in {
                *TERMINAL_RUN_STATUSES,
                RunStatus.PAUSED,
            }
        )
        else max(
            0,
            run.max_run_seconds - elapsed_seconds,
        )
    )
    work_active = (
        item is not None
        and item.status
        in {
            WorkStatus.QUEUED,
            WorkStatus.LEASED,
            WorkStatus.PAUSED,
        }
    )
    partial_results = [
        {
            "title": task.input_data.get("title") or task.question,
            "summary": task.output_data["summary"],
        }
        for task in run.tasks
        if (
            task.status == TaskStatus.COMPLETED
            and task.output_data.get("summary")
        )
    ]
    unavailable_sources = sum(
        len(task.output_data.get("source_fetch_failures", []))
        for task in run.tasks
    )
    limitations = []
    if directions_failed:
        limitations.append(
            f"Не завершено направлений: {directions_failed}."
        )
    if unavailable_sources:
        limitations.append(
            f"Не удалось загрузить источников: {unavailable_sources}."
        )
    if run.external_requests_used >= run.max_external_requests:
        limitations.append("Достигнут лимит внешних запросов.")
    if run.tokens_used >= run.max_tokens:
        limitations.append("Достигнут лимит токенов.")
    result = {
        "run_id": str(run.id),
        "status": run.status.value,
        "stage": stage,
        "activity": activity,
        "elapsed_seconds": elapsed_seconds,
        "remaining_seconds_upper_bound": (
            remaining_upper_bound
        ),
        "counters": {
            "directions": {
                "total": directions_total,
                "completed": directions_completed,
                "failed": directions_failed,
            },
            "sources": sources,
            "claims": claims,
            "verifications": {
                "total": claims,
                "completed": verifications,
            },
        },
        "actions": {
            "can_pause": bool(
                item
                and item.status
                in {WorkStatus.QUEUED, WorkStatus.LEASED}
                and not item.cancel_requested
                and not item.pause_requested
                and not finalizing
            ),
            "can_resume": bool(
                item
                and (
                    item.status == WorkStatus.PAUSED
                    or item.status == WorkStatus.FAILED
                    or (
                        item.status == WorkStatus.SUCCEEDED
                        and run.status
                        == RunStatus.COMPLETED_WITH_ERRORS
                    )
                )
            ),
            "can_finish": bool(
                work_active
                and directions_completed > 0
                and not finalizing
                and not item.cancel_requested
            ),
        },
        "partial_results": partial_results,
        "limitations": limitations,
    }

    if include_technical:
        events = list(
            session.scalars(
                select(OperationalEvent)
                .where(
                    OperationalEvent.run_id == run.id,
                    OperationalEvent.status.in_(
                        {
                            EventStatus.FAILED,
                            EventStatus.RETRYING,
                        }
                    ),
                )
                .order_by(
                    OperationalEvent.created_at.desc()
                )
                .limit(20)
            ).all()
        )
        task_errors = [
            {
                "task_id": str(task.id),
                "message": task.output_data.get(
                    "error",
                    {},
                ).get("message", "Unknown task error"),
            }
            for task in run.tasks
            if task.status == TaskStatus.FAILED
        ]
        result["technical"] = {
            "work_attempts": item.attempts if item else 0,
            "last_error": item.last_error if item else None,
            "task_errors": task_errors,
            "events": [
                {
                    "status": event.status.value,
                    "operation": event.operation,
                    "attempt": event.attempt,
                    "error_code": event.error_code,
                    "created_at": event.created_at,
                }
                for event in events
            ],
        }

    return result
