import uuid
import warnings

from sqlalchemy import select

from app.api import app
from app.db.models import (
    ApiRole,
    ResearchRun,
    ReviewerIdentity,
    Tenant,
    WorkItem,
    WorkStatus,
    WebhookDeliveryStatus,
    WebhookSubscription,
)
from app.db.session import SessionFactory
from app.multitenancy import (
    create_tenant,
    issue_api_identity,
    reviewer_subject,
)
from app.queue import (
    claim_next_work,
    finish_work,
    heartbeat_work,
)
from app.webhooks import deliver_next, signature


def _headers(token: str, key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}

    if key:
        headers["Idempotency-Key"] = key

    return headers


def main() -> None:
    warnings.filterwarnings(
        "ignore",
        message=(
            "Using `httpx` with "
            "`starlette.testclient`.*"
        ),
    )
    from fastapi.testclient import TestClient

    suffix = uuid.uuid4().hex[:10]
    slug_a = f"api-a-{suffix}"
    slug_b = f"api-b-{suffix}"

    with SessionFactory() as session:
        tenant_a = create_tenant(
            session,
            slug=slug_a,
            name="API Tenant A",
        )
        tenant_b = create_tenant(
            session,
            slug=slug_b,
            name="API Tenant B",
        )
        _, token_a = issue_api_identity(
            session,
            tenant=tenant_a,
            subject="admin",
            role=ApiRole.ADMIN,
        )
        _, token_b = issue_api_identity(
            session,
            tenant=tenant_b,
            subject="admin",
            role=ApiRole.ADMIN,
        )
        tenant_a_id = tenant_a.id
        tenant_b_id = tenant_b.id
        session.add(
            WebhookSubscription(
                tenant_id=tenant_a.id,
                url="https://example.com/webhook",
                secret="smoke-secret",
                events=["run.created"],
                active=True,
            )
        )
        session.commit()

    client = TestClient(app)
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "<title>Исследования</title>" in dashboard.text
    assert "API bearer token" not in dashboard.text
    assert 'id="login-form"' in dashboard.text
    assert 'id="research-form"' in dashboard.text
    assert "Активные" in dashboard.text
    assert "Готовые" in dashboard.text
    assert "Архив" in dashboard.text
    unauthenticated = client.get("/api/v1/runs")
    assert unauthenticated.status_code in {401, 403}

    first = client.post(
        "/api/v1/runs",
        headers=_headers(token_a, "create-run-a-0001"),
        json={"question": "Tenant A question?"},
    )
    assert first.status_code == 202, first.text
    replay = client.post(
        "/api/v1/runs",
        headers=_headers(token_a, "create-run-a-0001"),
        json={"question": "Tenant A question?"},
    )
    assert replay.status_code == 202
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == first.json()
    run_a_id = uuid.UUID(first.json()["run_id"])
    delivered_request = {}

    def capture(url, body, headers):
        delivered_request.update(
            {
                "url": url,
                "body": body,
                "headers": headers,
            }
        )

    with SessionFactory() as session:
        delivery = deliver_next(
            session,
            send_fn=capture,
        )
        assert delivery is not None
        assert (
            delivery.status
            == WebhookDeliveryStatus.DELIVERED
        )
        assert (
            delivered_request["headers"][
                "X-Deep-Research-Signature"
            ]
            == signature(
                "smoke-secret",
                delivered_request["body"],
            )
        )

    second = client.post(
        "/api/v1/runs",
        headers=_headers(token_b, "create-run-b-0001"),
        json={"question": "Tenant B question?"},
    )
    assert second.status_code == 202, second.text

    isolated = client.get(
        f"/api/v1/runs/{run_a_id}",
        headers=_headers(token_b),
    )
    assert isolated.status_code == 404
    visible = client.get(
        f"/api/v1/runs/{run_a_id}",
        headers=_headers(token_a),
    )
    assert visible.status_code == 200

    cancelled = client.post(
        f"/api/v1/runs/{run_a_id}/cancel",
        headers=_headers(token_a, "cancel-run-a-0001"),
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["work_status"] == "cancelled"

    with SessionFactory() as session:
        item = claim_next_work(
            session,
            worker_id="api-smoke-worker",
            lease_seconds=30,
        )
        assert item is not None
        assert item.tenant_id == tenant_b_id
        assert item.status == WorkStatus.LEASED
        assert not heartbeat_work(
            session,
            item_id=item.id,
            worker_id="wrong-worker",
            lease_seconds=30,
        )
        assert heartbeat_work(
            session,
            item_id=item.id,
            worker_id="api-smoke-worker",
            lease_seconds=30,
        )
        finished = finish_work(
            session,
            item_id=item.id,
            worker_id="api-smoke-worker",
            succeeded=True,
        )
        assert finished.status == WorkStatus.SUCCEEDED

        tenant_a = session.get(Tenant, tenant_a_id)
        tenant_b = session.get(Tenant, tenant_b_id)
        assert tenant_a is not None
        assert tenant_b is not None
        session.delete(tenant_a)
        session.delete(tenant_b)
        reviewers = list(
            session.scalars(
                select(ReviewerIdentity).where(
                    ReviewerIdentity.subject.in_(
                        {
                            reviewer_subject(
                                slug_a,
                                "admin",
                            ),
                            reviewer_subject(
                                slug_b,
                                "admin",
                            ),
                        }
                    )
                )
            ).all()
        )

        for reviewer in reviewers:
            session.delete(reviewer)

        session.commit()

    with SessionFactory() as session:
        assert session.get(ResearchRun, run_a_id) is None
        assert not session.scalar(
            select(WorkItem).where(
                WorkItem.run_id == run_a_id
            )
        )

    print(
        "Multi-tenant API, idempotency, queue, and "
        "cancellation smoke test OK"
    )


if __name__ == "__main__":
    main()
