from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiIdentity,
    ResearchRun,
    RunStatus,
    UserNotification,
)


def notify_users(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    title: str,
    message: str,
    run_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None,
    identity_ids: list[uuid.UUID] | None = None,
) -> None:
    statement = select(ApiIdentity.id).where(
        ApiIdentity.tenant_id == tenant_id,
        ApiIdentity.active.is_(True),
    )
    if identity_ids is not None:
        statement = statement.where(
            ApiIdentity.id.in_(identity_ids)
        )

    for identity_id in session.scalars(statement):
        session.add(
            UserNotification(
                tenant_id=tenant_id,
                identity_id=identity_id,
                run_id=run_id,
                draft_id=draft_id,
                kind=kind,
                title=title,
                message=message,
            )
        )


def notify_run_status(
    session: Session,
    run: ResearchRun,
) -> None:
    copy = {
        RunStatus.PAUSED: (
            "run_paused",
            "Исследование на паузе",
            "Собранные результаты сохранены. Работу можно продолжить.",
        ),
        RunStatus.COMPLETED: (
            "run_completed",
            "Исследование готово",
            "Отчёт готов к чтению.",
        ),
        RunStatus.COMPLETED_WITH_ERRORS: (
            "run_completed_with_errors",
            "Готов частичный результат",
            (
                "Полезные результаты сохранены. Незавершённые этапы "
                "можно повторить без дублей."
            ),
        ),
        RunStatus.FAILED: (
            "run_failed",
            "Исследование требует внимания",
            "Запуск остановился. Незавершённые этапы можно повторить.",
        ),
    }.get(run.status)
    if copy is None or run.tenant_id is None:
        return

    kind, title, message = copy
    notify_users(
        session,
        tenant_id=run.tenant_id,
        run_id=run.id,
        kind=kind,
        title=title,
        message=message,
    )
