from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ReviewerIdentity, ReviewerRole


ROLE_PERMISSIONS: dict[ReviewerRole, set[str]] = {
    ReviewerRole.VIEWER: {"view"},
    ReviewerRole.REVIEWER: {
        "view",
        "review_claim",
        "review_report",
    },
    ReviewerRole.PUBLISHER: {
        "view",
        "review_claim",
        "review_report",
        "publish",
        "export",
    },
    ReviewerRole.ADMIN: {
        "view",
        "review_claim",
        "review_report",
        "publish",
        "export",
        "manage_reviewers",
    },
}


def get_reviewer(
    session: Session,
    subject: str,
) -> ReviewerIdentity | None:
    return session.scalar(
        select(ReviewerIdentity).where(
            ReviewerIdentity.subject == subject.strip()
        )
    )


def authorize(
    session: Session,
    subject: str,
    permission: str,
) -> ReviewerIdentity:
    reviewer = get_reviewer(session, subject)

    if reviewer is None:
        raise PermissionError(
            f"Reviewer identity is not registered: {subject}"
        )

    if not reviewer.active:
        raise PermissionError(
            f"Reviewer identity is disabled: {subject}"
        )

    if permission not in ROLE_PERMISSIONS[reviewer.role]:
        raise PermissionError(
            f"Reviewer role {reviewer.role.value} cannot "
            f"perform {permission}"
        )

    return reviewer


def register_reviewer(
    session: Session,
    *,
    subject: str,
    display_name: str,
    role: ReviewerRole,
    actor: str | None,
) -> ReviewerIdentity:
    subject = subject.strip()
    display_name = display_name.strip()

    if not subject or not display_name:
        raise ValueError(
            "Reviewer subject and display name are required"
        )

    count = session.scalar(
        select(func.count(ReviewerIdentity.id))
    ) or 0

    if count:
        if not actor:
            raise PermissionError(
                "An admin actor is required"
            )
        authorize(session, actor, "manage_reviewers")
    elif role != ReviewerRole.ADMIN:
        raise PermissionError(
            "The first reviewer must have the admin role"
        )

    reviewer = get_reviewer(session, subject)

    if reviewer is None:
        reviewer = ReviewerIdentity(
            subject=subject,
            display_name=display_name,
            role=role,
            active=True,
        )
        session.add(reviewer)
    else:
        reviewer.display_name = display_name
        reviewer.role = role
        reviewer.active = True

    session.commit()
    session.refresh(reviewer)
    return reviewer


def set_reviewer_active(
    session: Session,
    *,
    subject: str,
    active: bool,
    actor: str,
) -> ReviewerIdentity:
    admin = authorize(
        session,
        actor,
        "manage_reviewers",
    )
    reviewer = get_reviewer(session, subject)

    if reviewer is None:
        raise RuntimeError(
            f"Reviewer identity not found: {subject}"
        )

    if reviewer.id == admin.id and not active:
        raise PermissionError(
            "An admin cannot disable their own identity"
        )

    reviewer.active = active
    session.commit()
    session.refresh(reviewer)
    return reviewer


def list_reviewers(
    session: Session,
) -> list[ReviewerIdentity]:
    return list(
        session.scalars(
            select(ReviewerIdentity).order_by(
                ReviewerIdentity.subject
            )
        ).all()
    )
