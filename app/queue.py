from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ClaimRecheckRequest,
    ClaimRecheckStatus,
    ResearchRun,
    ResearchTask,
    RunStatus,
    TaskStatus,
    WorkItem,
    WorkStatus,
)
from app.notifications import notify_run_status


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
        pause_requested=False,
        finish_requested=False,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def enqueue_claim_recheck(
    session: Session,
    *,
    request: ClaimRecheckRequest,
) -> WorkItem:
    item = session.scalar(
        select(WorkItem)
        .where(
            WorkItem.run_id == request.run_id,
            WorkItem.kind == "recheck_claim",
        )
        .with_for_update()
    )

    if (
        item is not None
        and item.status
        in {
            WorkStatus.QUEUED,
            WorkStatus.LEASED,
            WorkStatus.PAUSED,
        }
    ):
        raise RuntimeError(
            "Another claim recheck is already active "
            "for this research run"
        )

    if item is None:
        item = WorkItem(
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            kind="recheck_claim",
        )
        session.add(item)

    item.status = WorkStatus.QUEUED
    item.payload = {
        "recheck_id": str(request.id),
        "claim_id": str(request.claim_id),
    }
    item.attempts = 0
    item.max_attempts = 3
    item.available_at = datetime.now(timezone.utc)
    item.lease_owner = None
    item.lease_expires_at = None
    item.heartbeat_at = None
    item.cancel_requested = False
    item.pause_requested = False
    item.finish_requested = False
    item.last_error = None
    request.status = ClaimRecheckStatus.QUEUED
    session.flush()
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
    allow_finish_requested: bool = False,
) -> bool:
    item = session.get(WorkItem, item_id)

    if (
        item is None
        or item.status != WorkStatus.LEASED
        or item.lease_owner != worker_id
    ):
        return False

    if (
        item.cancel_requested
        or item.pause_requested
        or (
            item.finish_requested
            and not allow_finish_requested
        )
    ):
        return False

    now = datetime.now(timezone.utc)
    item.heartbeat_at = now
    item.lease_expires_at = now + timedelta(
        seconds=max(10, lease_seconds)
    )
    session.commit()
    return True


def request_run_pause(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> WorkItem:
    run, item = _control_target(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
    )

    if item.status == WorkStatus.PAUSED:
        return item

    if item.status not in {
        WorkStatus.QUEUED,
        WorkStatus.LEASED,
    }:
        raise RuntimeError(
            f"Cannot pause work item: {item.status.value}"
        )

    item.pause_requested = True
    run.status = RunStatus.PAUSE_REQUESTED

    if item.status == WorkStatus.QUEUED:
        item.status = WorkStatus.PAUSED
        run.status = RunStatus.PAUSED
        notify_run_status(session, run)

    session.flush()
    session.refresh(item)
    return item


def request_run_resume(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> WorkItem:
    run, item = _control_target(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        allow_retry=True,
    )

    if (
        item.status == WorkStatus.QUEUED
        and not item.pause_requested
    ):
        return item

    retryable_terminal = (
        item.status == WorkStatus.FAILED
        or (
            item.status == WorkStatus.SUCCEEDED
            and run.status == RunStatus.COMPLETED_WITH_ERRORS
        )
    )
    if item.status != WorkStatus.PAUSED and not retryable_terminal:
        raise RuntimeError(
            f"Cannot resume work item: {item.status.value}"
        )

    item.pause_requested = False
    item.finish_requested = False
    item.status = WorkStatus.QUEUED
    item.available_at = datetime.now(timezone.utc)
    item.attempts = 0
    item.last_error = None
    item.payload = {
        key: value
        for key, value in item.payload.items()
        if key != "finish_early"
    }
    run.status = RunStatus.CREATED
    session.flush()
    session.refresh(item)
    return item


def request_run_early_completion(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> WorkItem:
    run, item = _control_target(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    completed_tasks = session.scalar(
        select(func.count(ResearchTask.id)).where(
            ResearchTask.run_id == run_id,
            ResearchTask.status == TaskStatus.COMPLETED,
        )
    )

    if not completed_tasks:
        raise RuntimeError(
            "Cannot finish before any research direction completes"
        )

    if item.status not in {
        WorkStatus.QUEUED,
        WorkStatus.LEASED,
        WorkStatus.PAUSED,
    }:
        raise RuntimeError(
            f"Cannot finish work item: {item.status.value}"
        )

    item.pause_requested = False
    item.finish_requested = True

    if item.status in {
        WorkStatus.QUEUED,
        WorkStatus.PAUSED,
    }:
        item.status = WorkStatus.QUEUED
        item.available_at = datetime.now(timezone.utc)
        item.payload = {
            **item.payload,
            "finish_early": True,
        }
        run.status = RunStatus.CREATED

    session.flush()
    session.refresh(item)
    return item


def _control_target(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    allow_retry: bool = False,
) -> tuple[ResearchRun, WorkItem]:
    run = session.scalar(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.tenant_id == tenant_id,
        ).with_for_update()
    )

    if run is None:
        raise RuntimeError(f"Research run not found: {run_id}")

    item = session.scalar(
        select(WorkItem).where(
            WorkItem.run_id == run_id,
            WorkItem.tenant_id == tenant_id,
            WorkItem.kind == "execute_research_run",
        ).with_for_update()
    )

    if item is None:
        raise RuntimeError(
            f"Work item not found for run: {run_id}"
        )

    retryable_terminal = allow_retry and (
        item.status == WorkStatus.FAILED
        or (
            item.status == WorkStatus.SUCCEEDED
            and run.status == RunStatus.COMPLETED_WITH_ERRORS
        )
    )
    if (
        item.status
        in {
            WorkStatus.SUCCEEDED,
            WorkStatus.FAILED,
            WorkStatus.CANCELLED,
        }
        and not retryable_terminal
    ):
        raise RuntimeError(
            f"Cannot control terminal work item: "
            f"{item.status.value}"
        )

    return run, item


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
            WorkItem.run_id == run_id,
            WorkItem.kind == "execute_research_run",
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

    if item.status in {
        WorkStatus.QUEUED,
        WorkStatus.PAUSED,
    }:
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
    controls_run = item.kind == "execute_research_run"

    finalizing_early = bool(
        item.payload.get("finish_early")
    )

    if item.cancel_requested:
        item.status = WorkStatus.CANCELLED

        if controls_run and run is not None:
            run.status = RunStatus.CANCELLED
    elif succeeded:
        item.status = WorkStatus.SUCCEEDED
        item.pause_requested = False
        item.finish_requested = False
    elif item.pause_requested:
        item.status = WorkStatus.PAUSED
        item.pause_requested = False
        item.attempts = max(0, item.attempts - 1)

        if controls_run and run is not None:
            run.status = RunStatus.PAUSED
            notify_run_status(session, run)
    elif item.finish_requested and not finalizing_early:
        item.status = WorkStatus.QUEUED
        item.available_at = datetime.now(timezone.utc)
        item.payload = {
            **item.payload,
            "finish_early": True,
        }
        item.attempts = max(0, item.attempts - 1)

        if controls_run and run is not None:
            run.status = RunStatus.CREATED
    elif item.attempts < item.max_attempts:
        item.status = WorkStatus.QUEUED
        item.available_at = datetime.now(
            timezone.utc
        ) + timedelta(seconds=2 ** item.attempts)
    else:
        item.status = WorkStatus.FAILED

        if controls_run and run is not None:
            run.status = RunStatus.FAILED

        if item.kind == "recheck_claim":
            recheck_id = item.payload.get("recheck_id")

            if recheck_id:
                request = session.get(
                    ClaimRecheckRequest,
                    uuid.UUID(recheck_id),
                )

                if request is not None:
                    request.status = ClaimRecheckStatus.FAILED
                    request.error = (
                        error
                        or "Claim recheck failed"
                    )
                    request.completed_at = datetime.now(
                        timezone.utc
                    )

    item.last_error = error
    item.lease_owner = None
    item.lease_expires_at = None

    if (
        controls_run
        and run is not None
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
        notify_run_status(session, run)

    session.commit()
    session.refresh(item)
    return item
