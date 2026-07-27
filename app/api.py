from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    ApiIdentity,
    ApiRole,
    Claim,
    IdempotencyRecord,
    ResearchRun,
    ReviewDecisionType,
    RunStatus,
    Tenant,
    WorkItem,
    WorkStatus,
    WebhookSubscription,
)
from app.db.repositories import (
    get_claims_for_run,
    get_research_report,
    get_review_decisions_for_run,
    get_tasks_for_run,
)
from app.db.session import SessionFactory
from app.health import readiness
from app.multitenancy import (
    API_PERMISSIONS,
    authenticate_api_token,
    authenticate_browser_session,
    authenticate_password,
    authorize_api,
    create_browser_session,
    create_password_identity,
    reset_identity_password,
    revoke_browser_session,
    reviewer_subject,
)
from app.operations import (
    publish_report,
    review_claim,
    review_report,
)
from app.queue import request_run_cancellation
from app.webhooks import (
    enqueue_webhook_event,
    validate_webhook_url,
)
from app.source_store import PROJECT_ROOT


app = FastAPI(
    title="Deep Research API",
    version="1.0.0",
)
bearer = HTTPBearer(
    bearerFormat="DeepResearchToken",
    auto_error=False,
)


class CreateRunRequest(BaseModel):
    question: str = Field(min_length=3, max_length=10_000)


class LoginRequest(BaseModel):
    tenant: str = Field(min_length=1, max_length=100)
    login: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=256)


class CreateAccountRequest(BaseModel):
    login: str = Field(min_length=3, max_length=255)
    role: ApiRole
    password: str = Field(min_length=12, max_length=256)


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class ClaimReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "research"]
    reason: str = Field(min_length=3, max_length=5_000)


class ReportReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=5_000)


class PublishRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=5_000)


class WebhookRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2_000)
    events: list[str] = Field(
        default_factory=lambda: ["*"],
        min_length=1,
        max_length=20,
    )


def get_session() -> Generator[Session, None, None]:
    with SessionFactory() as session:
        yield session


SessionDependency = Annotated[
    Session,
    Depends(get_session),
]


def current_identity(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
    session: SessionDependency,
) -> ApiIdentity:
    if credentials is not None:
        try:
            return authenticate_api_token(
                session,
                credentials.credentials,
            )
        except PermissionError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(error),
            ) from error

    settings = get_settings()
    token = request.cookies.get(
        settings.session_cookie_name
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        identity, _ = authenticate_browser_session(
            session,
            token,
        )
    except PermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf_cookie = request.cookies.get(
            settings.csrf_cookie_name
        )
        csrf_header = request.headers.get("X-CSRF-Token")

        if (
            not csrf_cookie
            or not csrf_header
            or not secrets.compare_digest(
                csrf_cookie,
                csrf_header,
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid CSRF token",
            )

    return identity


IdentityDependency = Annotated[
    ApiIdentity,
    Depends(current_identity),
]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
]


def _require(
    identity: ApiIdentity,
    permission: str,
) -> None:
    try:
        authorize_api(identity, permission)
    except PermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


def _identity_payload(identity: ApiIdentity) -> dict:
    permissions = API_PERMISSIONS[identity.role]
    settings = get_settings()
    return {
        "id": str(identity.id),
        "tenant_id": str(identity.tenant_id),
        "login": identity.subject,
        "role": identity.role.value,
        "csrf_cookie_name": settings.csrf_cookie_name,
        "capabilities": {
            "create_run": "create_run" in permissions,
            "view_provenance": (
                "view_provenance" in permissions
            ),
            "review_claim": (
                "review_claim" in permissions
            ),
            "review_report": (
                "review_report" in permissions
            ),
            "publish": "publish" in permissions,
            "manage_accounts": (
                "manage_identities" in permissions
            ),
        },
    }


def _set_auth_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
) -> None:
    settings = get_settings()
    max_age = settings.session_lifetime_days * 86_400
    cookie_options = {
        "max_age": max_age,
        "secure": settings.session_cookie_secure,
        "samesite": "strict",
        "path": "/",
    }
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        httponly=True,
        **cookie_options,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        httponly=False,
        **cookie_options,
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()

    for name in (
        settings.session_cookie_name,
        settings.csrf_cookie_name,
    ):
        response.delete_cookie(
            name,
            path="/",
            secure=settings.session_cookie_secure,
            samesite="strict",
        )


def _request_hash(
    operation: str,
    payload: dict,
) -> str:
    encoded = json.dumps(
        {
            "operation": operation,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cached_idempotency(
    session: Session,
    *,
    identity: ApiIdentity,
    key: str,
    request_hash: str,
) -> JSONResponse | None:
    lock_key = int.from_bytes(
        hashlib.sha256(
            (
                str(identity.tenant_id)
                + ":"
                + key
            ).encode("utf-8")
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.execute(
        select(func.pg_advisory_xact_lock(lock_key))
    )
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id
            == identity.tenant_id,
            IdempotencyRecord.key == key,
        )
    )

    if record is None:
        return None

    if record.expires_at <= datetime.now(timezone.utc):
        session.delete(record)
        session.flush()
        return None

    if (
        record.identity_id != identity.id
        or record.request_hash != request_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Idempotency key was used for another "
                "request"
            ),
        )

    return JSONResponse(
        status_code=record.status_code,
        content=record.response_json,
        headers={"Idempotency-Replayed": "true"},
    )


def _store_idempotency(
    session: Session,
    *,
    identity: ApiIdentity,
    key: str,
    request_hash: str,
    response_json: dict,
    status_code: int,
) -> None:
    session.add(
        IdempotencyRecord(
            tenant_id=identity.tenant_id,
            identity_id=identity.id,
            key=key,
            request_hash=request_hash,
            response_json=response_json,
            status_code=status_code,
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=24),
        )
    )


def _tenant_run(
    session: Session,
    identity: ApiIdentity,
    run_id: uuid.UUID,
) -> ResearchRun:
    run = session.scalar(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.tenant_id == identity.tenant_id,
        )
    )

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )

    return run


def _tenant_reviewer(
    session: Session,
    identity: ApiIdentity,
) -> str:
    tenant = session.get(Tenant, identity.tenant_id)

    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant is unavailable",
        )

    return reviewer_subject(
        tenant.slug,
        identity.subject,
    )


@app.get("/health/live")
def live() -> dict:
    return {"status": "alive"}


@app.post("/api/v1/auth/login")
def login(
    body: LoginRequest,
    response: Response,
    session: SessionDependency,
) -> dict:
    try:
        identity = authenticate_password(
            session,
            tenant_slug=body.tenant,
            subject=body.login,
            password=body.password,
        )
    except PermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    settings = get_settings()
    _, session_token = create_browser_session(
        session,
        identity=identity,
        lifetime=timedelta(
            days=settings.session_lifetime_days
        ),
    )
    csrf_token = secrets.token_urlsafe(32)
    _set_auth_cookies(
        response,
        session_token=session_token,
        csrf_token=csrf_token,
    )
    return _identity_payload(identity)


@app.get("/api/v1/auth/session")
def get_auth_session(
    identity: IdentityDependency,
) -> dict:
    return _identity_payload(identity)


@app.post(
    "/api/v1/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout(
    request: Request,
    response: Response,
    identity: IdentityDependency,
    session: SessionDependency,
) -> Response:
    del identity
    settings = get_settings()
    token = request.cookies.get(
        settings.session_cookie_name
    )

    if token:
        revoke_browser_session(session, token)

    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@app.post(
    "/api/v1/admin/accounts",
    status_code=status.HTTP_201_CREATED,
)
def create_account(
    body: CreateAccountRequest,
    identity: IdentityDependency,
    session: SessionDependency,
) -> dict:
    _require(identity, "manage_identities")
    tenant = session.get(Tenant, identity.tenant_id)

    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant is unavailable",
        )

    try:
        created = create_password_identity(
            session,
            tenant=tenant,
            subject=body.login,
            role=body.role,
            password=body.password,
            actor=identity,
        )
    except (PermissionError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    return _identity_payload(created)


@app.post(
    "/api/v1/admin/accounts/{identity_id}/reset-password"
)
def reset_account_password(
    identity_id: uuid.UUID,
    body: ResetPasswordRequest,
    identity: IdentityDependency,
    session: SessionDependency,
) -> dict:
    _require(identity, "manage_identities")

    try:
        updated = reset_identity_password(
            session,
            actor=identity,
            identity_id=identity_id,
            password=body.password,
        )
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Identity not found",
        ) from error
    except (PermissionError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    return _identity_payload(updated)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return (
        PROJECT_ROOT
        / "app"
        / "static"
        / "dashboard.html"
    ).read_text(encoding="utf-8")


@app.get("/health/ready")
def ready(response: Response) -> dict:
    result = readiness()

    if result["status"] != "ready":
        response.status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )

    return result


@app.post(
    "/api/v1/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(
    body: CreateRunRequest,
    idempotency_key: IdempotencyKey,
    identity: IdentityDependency,
    session: SessionDependency,
):
    _require(identity, "create_run")
    payload = body.model_dump(mode="json")
    request_hash = _request_hash("create_run", payload)
    cached = _cached_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if cached is not None:
        return cached

    settings = get_settings()
    run = ResearchRun(
        tenant_id=identity.tenant_id,
        created_by_identity_id=identity.id,
        question=body.question.strip(),
        status=RunStatus.CREATED,
        max_external_requests=settings.max_external_requests,
        max_sources=settings.max_sources,
        max_claims=settings.max_claims,
        max_tokens=settings.max_tokens,
        max_run_seconds=settings.max_run_seconds,
    )
    session.add(run)
    session.flush()
    item = WorkItem(
        tenant_id=identity.tenant_id,
        run_id=run.id,
        kind="execute_research_run",
        status=WorkStatus.QUEUED,
        payload={"run_id": str(run.id)},
        attempts=0,
        max_attempts=3,
        cancel_requested=False,
    )
    session.add(item)
    session.flush()
    result = {
        "run_id": str(run.id),
        "work_item_id": str(item.id),
        "status": run.status.value,
    }
    _store_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
        response_json=result,
        status_code=status.HTTP_202_ACCEPTED,
    )
    enqueue_webhook_event(
        session,
        tenant_id=identity.tenant_id,
        run_id=run.id,
        event_type="run.created",
        payload=result,
    )

    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent idempotent request conflict",
        ) from error

    return result


@app.get("/api/v1/runs")
def list_runs(
    identity: IdentityDependency,
    session: SessionDependency,
) -> list[dict]:
    _require(identity, "view")
    runs = list(
        session.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.tenant_id
                == identity.tenant_id
            )
            .order_by(ResearchRun.created_at.desc())
            .limit(100)
        ).all()
    )
    return [
        {
            "id": str(run.id),
            "question": run.question,
            "status": run.status.value,
            "author": (
                run.created_by.subject
                if run.created_by is not None
                else None
            ),
            "created_at": run.created_at,
        }
        for run in runs
    ]


@app.get("/api/v1/runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    identity: IdentityDependency,
    session: SessionDependency,
) -> dict:
    _require(identity, "view")
    run = _tenant_run(session, identity, run_id)
    return {
        "id": str(run.id),
        "question": run.question,
        "status": run.status.value,
        "author": (
            run.created_by.subject
            if run.created_by is not None
            else None
        ),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


@app.get("/api/v1/runs/{run_id}/provenance")
def get_provenance(
    run_id: uuid.UUID,
    identity: IdentityDependency,
    session: SessionDependency,
) -> dict:
    _require(identity, "view_provenance")
    run = _tenant_run(session, identity, run_id)
    report = get_research_report(session, run.id)
    return {
        "run_id": str(run.id),
        "tasks": [
            {
                "id": str(task.id),
                "status": task.status.value,
                "question": task.question,
                "output": task.output_data,
            }
            for task in get_tasks_for_run(session, run.id)
        ],
        "claims": [
            {
                "id": str(claim.id),
                "text": claim.text,
                "status": claim.status.value,
                "review_status": (
                    claim.review_status.value
                ),
                "source_snapshot_id": (
                    str(claim.source_snapshot_id)
                    if claim.source_snapshot_id
                    else None
                ),
            }
            for claim in get_claims_for_run(session, run.id)
        ],
        "review_decisions": [
            {
                "target_type": entry.target_type.value,
                "target_id": str(entry.target_id),
                "decision": entry.decision.value,
                "reason": entry.reason,
                "reviewer": entry.reviewer,
                "created_at": entry.created_at,
            }
            for entry in get_review_decisions_for_run(
                session,
                run.id,
            )
        ],
        "report": (
            None
            if report is None
            else {
                "review_status": (
                    report.review_status.value
                ),
                "result": report.result_json,
            }
        ),
    }


@app.post(
    "/api/v1/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_run(
    run_id: uuid.UUID,
    idempotency_key: IdempotencyKey,
    identity: IdentityDependency,
    session: SessionDependency,
):
    _require(identity, "cancel_run")
    _tenant_run(session, identity, run_id)
    request_hash = _request_hash(
        "cancel_run",
        {"run_id": str(run_id)},
    )
    cached = _cached_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if cached is not None:
        return cached

    try:
        item = request_run_cancellation(
            session,
            tenant_id=identity.tenant_id,
            run_id=run_id,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    result = {
        "run_id": str(run_id),
        "work_status": item.status.value,
        "cancel_requested": item.cancel_requested,
    }
    _store_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
        response_json=result,
        status_code=status.HTTP_202_ACCEPTED,
    )
    enqueue_webhook_event(
        session,
        tenant_id=identity.tenant_id,
        run_id=run_id,
        event_type="run.cancel_requested",
        payload=result,
    )
    session.commit()
    return result


def _tenant_claim(
    session: Session,
    identity: ApiIdentity,
    claim_id: uuid.UUID,
) -> Claim:
    claim = session.scalar(
        select(Claim)
        .join(ResearchRun)
        .where(
            Claim.id == claim_id,
            ResearchRun.tenant_id == identity.tenant_id,
        )
    )

    if claim is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Claim not found",
        )

    return claim


@app.post("/api/v1/claims/{claim_id}/review")
def api_review_claim(
    claim_id: uuid.UUID,
    body: ClaimReviewRequest,
    idempotency_key: IdempotencyKey,
    identity: IdentityDependency,
    session: SessionDependency,
):
    _require(identity, "review_claim")
    claim = _tenant_claim(session, identity, claim_id)
    payload = {
        "claim_id": str(claim_id),
        **body.model_dump(mode="json"),
    }
    request_hash = _request_hash("review_claim", payload)
    cached = _cached_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if cached is not None:
        return cached

    decision = {
        "approve": ReviewDecisionType.APPROVE,
        "reject": ReviewDecisionType.REJECT,
        "research": (
            ReviewDecisionType.REQUEST_RESEARCH
        ),
    }[body.decision]
    reviewer = _tenant_reviewer(session, identity)

    if decision == ReviewDecisionType.REQUEST_RESEARCH:
        from app.operations import (
            request_additional_research,
        )

        request_additional_research(
            claim.id,
            reason=body.reason,
            reviewer=reviewer,
        )
    else:
        review_claim(
            claim.id,
            decision=decision,
            reason=body.reason,
            reviewer=reviewer,
        )

    result = {
        "claim_id": str(claim.id),
        "decision": body.decision,
    }
    _store_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
        response_json=result,
        status_code=200,
    )
    enqueue_webhook_event(
        session,
        tenant_id=identity.tenant_id,
        run_id=claim.run_id,
        event_type="claim.reviewed",
        payload=result,
    )
    session.commit()
    return result


@app.post("/api/v1/runs/{run_id}/review")
def api_review_report(
    run_id: uuid.UUID,
    body: ReportReviewRequest,
    idempotency_key: IdempotencyKey,
    identity: IdentityDependency,
    session: SessionDependency,
):
    _require(identity, "review_report")
    _tenant_run(session, identity, run_id)
    payload = {
        "run_id": str(run_id),
        **body.model_dump(mode="json"),
    }
    request_hash = _request_hash("review_report", payload)
    cached = _cached_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if cached is not None:
        return cached

    review_report(
        run_id,
        decision=(
            ReviewDecisionType.APPROVE
            if body.decision == "approve"
            else ReviewDecisionType.REJECT
        ),
        reason=body.reason,
        reviewer=_tenant_reviewer(session, identity),
    )
    result = {
        "run_id": str(run_id),
        "decision": body.decision,
    }
    _store_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
        response_json=result,
        status_code=200,
    )
    enqueue_webhook_event(
        session,
        tenant_id=identity.tenant_id,
        run_id=run_id,
        event_type="report.reviewed",
        payload=result,
    )
    session.commit()
    return result


@app.post("/api/v1/runs/{run_id}/publish")
def api_publish_report(
    run_id: uuid.UUID,
    body: PublishRequest,
    idempotency_key: IdempotencyKey,
    identity: IdentityDependency,
    session: SessionDependency,
):
    _require(identity, "publish")
    _tenant_run(session, identity, run_id)
    payload = {
        "run_id": str(run_id),
        **body.model_dump(mode="json"),
    }
    request_hash = _request_hash("publish", payload)
    cached = _cached_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if cached is not None:
        return cached

    markdown_path, json_path = publish_report(
        run_id,
        reason=body.reason,
        reviewer=_tenant_reviewer(session, identity),
    )
    result = {
        "run_id": str(run_id),
        "status": "published",
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
    }
    _store_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
        response_json=result,
        status_code=200,
    )
    enqueue_webhook_event(
        session,
        tenant_id=identity.tenant_id,
        run_id=run_id,
        event_type="report.published",
        payload=result,
    )
    session.commit()
    return result


@app.get("/api/v1/webhooks")
def list_webhooks(
    identity: IdentityDependency,
    session: SessionDependency,
) -> list[dict]:
    _require(identity, "view")
    subscriptions = list(
        session.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.tenant_id
                == identity.tenant_id
            )
        ).all()
    )
    return [
        {
            "id": str(subscription.id),
            "url": subscription.url,
            "events": subscription.events,
            "active": subscription.active,
        }
        for subscription in subscriptions
    ]


@app.post(
    "/api/v1/webhooks",
    status_code=status.HTTP_201_CREATED,
)
def create_webhook(
    body: WebhookRequest,
    idempotency_key: IdempotencyKey,
    identity: IdentityDependency,
    session: SessionDependency,
):
    _require(identity, "manage_identities")
    payload = body.model_dump(mode="json")
    request_hash = _request_hash("create_webhook", payload)
    cached = _cached_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if cached is not None:
        return cached

    try:
        webhook_url = validate_webhook_url(body.url)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    subscription = WebhookSubscription(
        tenant_id=identity.tenant_id,
        url=webhook_url,
        secret=secrets.token_urlsafe(32),
        events=list(dict.fromkeys(body.events)),
        active=True,
    )
    session.add(subscription)
    session.flush()
    result = {
        "id": str(subscription.id),
        "url": subscription.url,
        "events": subscription.events,
        "secret": subscription.secret,
        "warning": "The signing secret is shown only once.",
    }
    _store_idempotency(
        session,
        identity=identity,
        key=idempotency_key,
        request_hash=request_hash,
        response_json=result,
        status_code=status.HTTP_201_CREATED,
    )
    session.commit()
    return result
