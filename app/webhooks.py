from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import socket
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookSubscription,
)
from app.db.session import SessionFactory


def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url.strip())

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "Webhook URL must use http or https"
        )

    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                parsed.hostname,
                parsed.port
                or (443 if parsed.scheme == "https" else 80),
            )
        }
    except socket.gaierror as error:
        raise ValueError(
            "Webhook hostname cannot be resolved"
        ) from error

    for value in addresses:
        address = ipaddress.ip_address(value)

        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
        ):
            raise ValueError(
                "Webhook URL resolves to a non-public address"
            )

    return url.strip()


def signature(secret: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def enqueue_webhook_event(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> list[WebhookDelivery]:
    subscriptions = list(
        session.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.tenant_id == tenant_id,
                WebhookSubscription.active.is_(True),
            )
        ).all()
    )
    event_id = uuid.uuid4()
    deliveries = []

    for subscription in subscriptions:
        if (
            event_type not in subscription.events
            and "*" not in subscription.events
        ):
            continue

        delivery = WebhookDelivery(
            tenant_id=tenant_id,
            subscription_id=subscription.id,
            run_id=run_id,
            event_id=event_id,
            event_type=event_type,
            payload={
                "event_id": str(event_id),
                "event_type": event_type,
                "run_id": str(run_id),
                "data": payload,
            },
            status=WebhookDeliveryStatus.PENDING,
            attempts=0,
            max_attempts=5,
        )
        session.add(delivery)
        deliveries.append(delivery)

    return deliveries


def deliver_next(
    session: Session,
    *,
    send_fn: Callable[
        [str, bytes, dict[str, str]],
        None,
    ]
    | None = None,
) -> WebhookDelivery | None:
    delivery = session.scalar(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status
            == WebhookDeliveryStatus.PENDING,
            WebhookDelivery.next_attempt_at <= func.now(),
        )
        .order_by(
            WebhookDelivery.next_attempt_at,
            WebhookDelivery.created_at,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )

    if delivery is None:
        return None

    subscription = delivery.subscription
    body = json.dumps(
        delivery.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Deep-Research-Event": delivery.event_type,
        "X-Deep-Research-Delivery": str(delivery.id),
        "X-Deep-Research-Signature": signature(
            subscription.secret,
            body,
        ),
    }
    delivery.attempts += 1

    try:
        if send_fn is None:
            url = validate_webhook_url(subscription.url)
            response = httpx.post(
                url,
                content=body,
                headers=headers,
                timeout=10,
                follow_redirects=False,
            )
            response.raise_for_status()
        else:
            send_fn(subscription.url, body, headers)
    except Exception as error:
        delivery.last_error = (
            f"{type(error).__name__}: {error}"
        )[:2_000]

        if delivery.attempts >= delivery.max_attempts:
            delivery.status = WebhookDeliveryStatus.FAILED
        else:
            delivery.next_attempt_at = datetime.now(
                timezone.utc
            ) + timedelta(
                seconds=min(
                    300,
                    2 ** delivery.attempts,
                )
            )
    else:
        delivery.status = WebhookDeliveryStatus.DELIVERED
        delivery.delivered_at = datetime.now(timezone.utc)
        delivery.last_error = None

    session.commit()
    session.refresh(delivery)
    return delivery


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deliver signed webhook events."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=2,
    )
    arguments = parser.parse_args(argv)

    while True:
        with SessionFactory() as session:
            delivery = deliver_next(session)

        if arguments.once:
            return (
                0
                if delivery is None
                or delivery.status
                == WebhookDeliveryStatus.DELIVERED
                else 1
            )

        if delivery is None:
            time.sleep(max(0.1, arguments.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
