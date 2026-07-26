from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ResearchRun,
    RunStatus,
    WorkItem,
    WorkStatus,
)


def enqueue_run(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> WorkItem:
    existing = session.scalar(
        select(WorkItem).where(
            WorkItem.run_id == run_id,
            WorkItem.kind == "execute_research_run",
        )
    )

    if existing is not None:
        return existing

    item = WorkItem(
        tenant_id=tenant_id,
        run_id=run_id,
        kind="execute_research_run",
        status=WorkStatus.QUEUED,
        payload={"run_id": str(run_id)},
        attempts=0,
        max_attempts=3,
        cancel_requested=False,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def claim_next_work(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 60,
) -> WorkItem | None:
    now = datetime.now(timezone.utc)
    statement = (
        select(WorkItem)
        .where(
            WorkItem.available_at <= func.now(),
            WorkItem.cancel_requested.is_(False),
            WorkItem.attempts < WorkItem.max_attempts,
            or_(
                WorkItem.status == WorkStatus.QUEUED,
                (
                    (WorkItem.status == WorkStatus.LEASED)
                    & (
                        WorkItem.lease_expires_at
                        < func.now()
                    )
                ),
            ),
        )
        .order_by(WorkItem.available_at, WorkItem.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    item = session.scalar(statement)

    if item is None:
        return None

    item.status = WorkStatus.LEASED
    item.lease_owner = worker_id
    item.lease_expires_at = now + timedelta(
        seconds=max(10, lease_seconds)
    )
    item.heartbeat_at = now
    item.attempts += 1
    session.commit()
    session.refresh(item)
    return item


def heartbeat_work(
    session: Session,
    *,
    item_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int = 60,
) -> bool:
    item = session.get(WorkItem, item_id)

    if (
        item is None
        or item.status != WorkStatus.LEASED
        or item.lease_owner != worker_id
    ):
        return False

    if item.cancel_requested:
        return False

    now = datetime.now(timezone.utc)
    item.heartbeat_at = now
    item.lease_expires_at = now + timedelta(
        seconds=max(10, lease_seconds)
    )
    session.commit()
    return True


def request_run_cancellation(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> WorkItem:
    run = session.scalar(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.tenant_id == tenant_id,
        )
    )

    if run is None:
        raise RuntimeError(f"Research run not found: {run_id}")

    item = session.scalar(
        select(WorkItem).where(
            WorkItem.run_id == run_id
        )
    )

    if item is None:
        raise RuntimeError(
            f"Work item not found for run: {run_id}"
        )

    if item.status in {
        WorkStatus.SUCCEEDED,
        WorkStatus.FAILED,
        WorkStatus.CANCELLED,
    }:
        raise RuntimeError(
            f"Cannot cancel terminal work item: "
            f"{item.status.value}"
        )

    item.cancel_requested = True
    run.status = RunStatus.CANCEL_REQUESTED

    if item.status == WorkStatus.QUEUED:
        item.status = WorkStatus.CANCELLED
        run.status = RunStatus.CANCELLED

    session.commit()
    session.refresh(item)
    return item


def finish_work(
    session: Session,
    *,
    item_id: uuid.UUID,
    worker_id: str,
    succeeded: bool,
    error: str | None = None,
) -> WorkItem:
    item = session.get(WorkItem, item_id)

    if (
        item is None
        or item.status != WorkStatus.LEASED
        or item.lease_owner != worker_id
    ):
        raise RuntimeError("Worker no longer owns the lease")

    run = session.get(ResearchRun, item.run_id)

    if item.cancel_requested:
        item.status = WorkStatus.CANCELLED

        if run is not None:
            run.status = RunStatus.CANCELLED
    elif succeeded:
        item.status = WorkStatus.SUCCEEDED
    elif item.attempts < item.max_attempts:
        item.status = WorkStatus.QUEUED
        item.available_at = datetime.now(
            timezone.utc
        ) + timedelta(seconds=2 ** item.attempts)
    else:
        item.status = WorkStatus.FAILED

        if run is not None:
            run.status = RunStatus.FAILED

    item.last_error = error
    item.lease_owner = None
    item.lease_expires_at = None

    if (
        run is not None
        and item.status
        in {
            WorkStatus.SUCCEEDED,
            WorkStatus.FAILED,
            WorkStatus.CANCELLED,
        }
    ):
        from app.webhooks import enqueue_webhook_event

        enqueue_webhook_event(
            session,
            tenant_id=item.tenant_id,
            run_id=item.run_id,
            event_type={
                WorkStatus.SUCCEEDED: "run.completed",
                WorkStatus.FAILED: "run.failed",
                WorkStatus.CANCELLED: "run.cancelled",
            }[item.status],
            payload={
                "run_id": str(item.run_id),
                "run_status": run.status.value,
                "work_status": item.status.value,
            },
        )

    session.commit()
    session.refresh(item)
    return item
