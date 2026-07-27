from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiIdentity,
    ApiRole,
    BrowserSession,
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
        "view_provenance",
        "review_claim",
        "review_report",
    },
    ApiRole.PUBLISHER: {
        "view",
        "view_provenance",
        "review_claim",
        "review_report",
        "publish",
    },
    ApiRole.ADMIN: {
        "view",
        "create_run",
        "cancel_run",
        "view_provenance",
        "review_claim",
        "review_report",
        "publish",
        "manage_identities",
    },
}

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_api_token(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def normalize_subject(subject: str) -> str:
    normalized = subject.strip().lower()

    if (
        len(normalized) < 3
        or len(normalized) > 255
        or any(
            not (
                character.isalnum()
                or character in "-_.@"
            )
            for character in normalized
        )
    ):
        raise ValueError(
            "Login must contain 3-255 letters, digits, "
            "dots, hyphens, underscores, or @"
        )

    return normalized


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
) -> str:
    if not (
        PASSWORD_MIN_LENGTH
        <= len(password)
        <= PASSWORD_MAX_LENGTH
    ):
        raise ValueError(
            "Password must contain 12-256 characters"
        )

    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(
    password: str,
    encoded: str,
) -> bool:
    try:
        algorithm, n, r, p, salt, expected = (
            encoded.split("$")
        )

        if (
            algorithm != "scrypt"
            or int(n) != SCRYPT_N
            or int(r) != SCRYPT_R
            or int(p) != SCRYPT_P
        ):
            return False

        decoded_salt = base64.b64decode(
            salt,
            validate=True,
        )
        decoded_expected = base64.b64decode(
            expected,
            validate=True,
        )

        if (
            len(decoded_salt) != 16
            or len(decoded_expected) != 32
        ):
            return False

        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=decoded_salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=32,
        )
        return hmac.compare_digest(
            digest,
            decoded_expected,
        )
    except (binascii.Error, TypeError, ValueError):
        return False


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


def authenticate_password(
    session: Session,
    *,
    tenant_slug: str,
    subject: str,
    password: str,
) -> ApiIdentity:
    try:
        normalized_subject = normalize_subject(subject)
    except ValueError:
        normalized_subject = ""

    identity = session.scalar(
        select(ApiIdentity)
        .join(Tenant)
        .where(
            Tenant.slug == tenant_slug.strip().lower(),
            ApiIdentity.subject == normalized_subject,
            ApiIdentity.active.is_(True),
            Tenant.active.is_(True),
        )
    )
    encoded = (
        identity.password_hash
        if identity is not None
        and identity.password_hash is not None
        else DUMMY_PASSWORD_HASH
    )
    valid = verify_password(password, encoded)

    if (
        not valid
        or identity is None
        or identity.password_hash is None
    ):
        raise PermissionError("Invalid credentials")

    return identity


def create_browser_session(
    session: Session,
    *,
    identity: ApiIdentity,
    lifetime: timedelta,
) -> tuple[BrowserSession, str]:
    token = "drs_" + secrets.token_urlsafe(32)
    record = BrowserSession(
        tenant_id=identity.tenant_id,
        identity_id=identity.id,
        token_hash=hash_api_token(token),
        expires_at=datetime.now(timezone.utc) + lifetime,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, token


def authenticate_browser_session(
    session: Session,
    token: str,
    *,
    now: datetime | None = None,
) -> tuple[ApiIdentity, BrowserSession]:
    now = now or datetime.now(timezone.utc)
    record = session.scalar(
        select(BrowserSession)
        .join(ApiIdentity)
        .join(Tenant)
        .where(
            BrowserSession.token_hash
            == hash_api_token(token),
            BrowserSession.revoked_at.is_(None),
            BrowserSession.expires_at > now,
            ApiIdentity.active.is_(True),
            Tenant.active.is_(True),
        )
    )

    if record is None:
        raise PermissionError("Invalid or expired session")

    return record.identity, record


def revoke_browser_session(
    session: Session,
    token: str,
) -> bool:
    record = session.scalar(
        select(BrowserSession).where(
            BrowserSession.token_hash
            == hash_api_token(token),
            BrowserSession.revoked_at.is_(None),
        )
    )

    if record is None:
        return False

    record.revoked_at = datetime.now(timezone.utc)
    session.commit()
    return True


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


def _upsert_reviewer(
    session: Session,
    *,
    tenant: Tenant,
    subject: str,
    role: ApiRole,
) -> None:
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


def create_password_identity(
    session: Session,
    *,
    tenant: Tenant,
    subject: str,
    role: ApiRole,
    password: str,
    actor: ApiIdentity | None = None,
) -> ApiIdentity:
    subject = normalize_subject(subject)
    count = session.scalar(
        select(func.count(ApiIdentity.id)).where(
            ApiIdentity.tenant_id == tenant.id
        )
    ) or 0

    if count:
        if actor is None:
            raise PermissionError(
                "An admin identity is required"
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

    identity = ApiIdentity(
        tenant_id=tenant.id,
        subject=subject,
        role=role,
        token_hash=None,
        password_hash=hash_password(password),
        active=True,
    )
    session.add(identity)
    _upsert_reviewer(
        session,
        tenant=tenant,
        subject=subject,
        role=role,
    )
    session.commit()
    session.refresh(identity)
    return identity


def reset_identity_password(
    session: Session,
    *,
    actor: ApiIdentity,
    identity_id: uuid.UUID,
    password: str,
) -> ApiIdentity:
    authorize_api(actor, "manage_identities")
    identity = session.scalar(
        select(ApiIdentity).where(
            ApiIdentity.id == identity_id,
            ApiIdentity.tenant_id == actor.tenant_id,
        )
    )

    if identity is None:
        raise LookupError("Identity not found")

    identity.password_hash = hash_password(password)
    now = datetime.now(timezone.utc)

    for browser_session in session.scalars(
        select(BrowserSession).where(
            BrowserSession.identity_id == identity.id,
            BrowserSession.revoked_at.is_(None),
        )
    ):
        browser_session.revoked_at = now

    session.commit()
    session.refresh(identity)
    return identity


def issue_api_identity(
    session: Session,
    *,
    tenant: Tenant,
    subject: str,
    role: ApiRole,
    actor_token: str | None = None,
) -> tuple[ApiIdentity, str]:
    subject = normalize_subject(subject)

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
    session.add(identity)
    _upsert_reviewer(
        session,
        tenant=tenant,
        subject=subject,
        role=role,
    )
    session.commit()
    session.refresh(identity)
    return identity, token


DUMMY_PASSWORD_HASH = hash_password(
    "not-a-real-password"
)
