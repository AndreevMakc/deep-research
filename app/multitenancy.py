from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiIdentity,
    ApiRole,
    ReviewerIdentity,
    ReviewerRole,
    Tenant,
)


API_PERMISSIONS: dict[ApiRole, set[str]] = {
    ApiRole.VIEWER: {"view"},
    ApiRole.RESEARCHER: {
        "view",
        "create_run",
        "cancel_run",
    },
    ApiRole.REVIEWER: {
        "view",
        "review_claim",
        "review_report",
    },
    ApiRole.PUBLISHER: {
        "view",
        "review_claim",
        "review_report",
        "publish",
    },
    ApiRole.ADMIN: {
        "view",
        "create_run",
        "cancel_run",
        "review_claim",
        "review_report",
        "publish",
        "manage_identities",
    },
}


def hash_api_token(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def create_tenant(
    session: Session,
    *,
    slug: str,
    name: str,
) -> Tenant:
    slug = slug.strip().lower()
    name = name.strip()

    if (
        not slug
        or not name
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for character in slug
        )
    ):
        raise ValueError(
            "Tenant slug must contain lowercase letters, "
            "digits, or hyphens"
        )

    existing = session.scalar(
        select(Tenant).where(Tenant.slug == slug)
    )

    if existing is not None:
        raise ValueError(f"Tenant already exists: {slug}")

    tenant = Tenant(slug=slug, name=name, active=True)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant


def authenticate_api_token(
    session: Session,
    token: str,
) -> ApiIdentity:
    identity = session.scalar(
        select(ApiIdentity)
        .join(Tenant)
        .where(
            ApiIdentity.token_hash
            == hash_api_token(token),
            ApiIdentity.active.is_(True),
            Tenant.active.is_(True),
        )
    )

    if identity is None:
        raise PermissionError("Invalid or disabled API token")

    return identity


def authorize_api(
    identity: ApiIdentity,
    permission: str,
) -> None:
    if permission not in API_PERMISSIONS[identity.role]:
        raise PermissionError(
            f"API role {identity.role.value} cannot "
            f"perform {permission}"
        )


def reviewer_subject(
    tenant_slug: str,
    subject: str,
) -> str:
    return f"{tenant_slug}:{subject}"


def _reviewer_role(role: ApiRole) -> ReviewerRole:
    return {
        ApiRole.VIEWER: ReviewerRole.VIEWER,
        ApiRole.RESEARCHER: ReviewerRole.VIEWER,
        ApiRole.REVIEWER: ReviewerRole.REVIEWER,
        ApiRole.PUBLISHER: ReviewerRole.PUBLISHER,
        ApiRole.ADMIN: ReviewerRole.ADMIN,
    }[role]


def issue_api_identity(
    session: Session,
    *,
    tenant: Tenant,
    subject: str,
    role: ApiRole,
    actor_token: str | None = None,
) -> tuple[ApiIdentity, str]:
    subject = subject.strip()

    if not subject:
        raise ValueError("Identity subject is required")

    count = session.scalar(
        select(func.count(ApiIdentity.id)).where(
            ApiIdentity.tenant_id == tenant.id
        )
    ) or 0

    if count:
        if not actor_token:
            raise PermissionError(
                "An admin API token is required"
            )
        actor = authenticate_api_token(
            session,
            actor_token,
        )

        if actor.tenant_id != tenant.id:
            raise PermissionError(
                "Admin belongs to another tenant"
            )
        authorize_api(actor, "manage_identities")
    elif role != ApiRole.ADMIN:
        raise PermissionError(
            "The first tenant identity must be admin"
        )

    if session.scalar(
        select(ApiIdentity).where(
            ApiIdentity.tenant_id == tenant.id,
            ApiIdentity.subject == subject,
        )
    ):
        raise ValueError(
            f"API identity already exists: {subject}"
        )

    token = "dr_" + secrets.token_urlsafe(32)
    identity = ApiIdentity(
        tenant_id=tenant.id,
        subject=subject,
        role=role,
        token_hash=hash_api_token(token),
        active=True,
    )
    namespaced_subject = reviewer_subject(
        tenant.slug,
        subject,
    )
    reviewer = session.scalar(
        select(ReviewerIdentity).where(
            ReviewerIdentity.subject
            == namespaced_subject
        )
    )

    if reviewer is None:
        reviewer = ReviewerIdentity(
            subject=namespaced_subject,
            display_name=subject,
            role=_reviewer_role(role),
            active=True,
        )
        session.add(reviewer)
    else:
        reviewer.role = _reviewer_role(role)
        reviewer.active = True

    session.add(identity)
    session.commit()
    session.refresh(identity)
    return identity, token
