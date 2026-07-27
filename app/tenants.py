from __future__ import annotations

import argparse
import getpass
import json
import sys

from sqlalchemy import select

from app.db.models import ApiRole, Tenant
from app.db.session import SessionFactory
from app.multitenancy import (
    authenticate_api_token,
    create_password_identity,
    create_tenant,
    issue_api_identity,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage tenants and API identities."
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )
    tenant = subparsers.add_parser("create")
    tenant.add_argument("slug")
    tenant.add_argument("name")
    identity = subparsers.add_parser("issue-token")
    identity.add_argument("tenant_slug")
    identity.add_argument("subject")
    identity.add_argument(
        "--role",
        required=True,
        choices=tuple(role.value for role in ApiRole),
    )
    identity.add_argument("--actor-token")
    user = subparsers.add_parser("create-user")
    user.add_argument("tenant_slug")
    user.add_argument("subject")
    user.add_argument(
        "--role",
        required=True,
        choices=tuple(role.value for role in ApiRole),
    )
    user.add_argument("--actor-token")
    user.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from standard input.",
    )
    arguments = parser.parse_args(argv)

    try:
        with SessionFactory() as session:
            if arguments.command == "create":
                created = create_tenant(
                    session,
                    slug=arguments.slug,
                    name=arguments.name,
                )
                result = {
                    "id": str(created.id),
                    "slug": created.slug,
                    "name": created.name,
                }
            elif arguments.command == "issue-token":
                tenant_record = session.scalar(
                    select(Tenant).where(
                        Tenant.slug
                        == arguments.tenant_slug
                    )
                )

                if tenant_record is None:
                    raise RuntimeError(
                        "Tenant not found: "
                        f"{arguments.tenant_slug}"
                    )

                issued, token = issue_api_identity(
                    session,
                    tenant=tenant_record,
                    subject=arguments.subject,
                    role=ApiRole(arguments.role),
                    actor_token=arguments.actor_token,
                )
                result = {
                    "identity_id": str(issued.id),
                    "tenant": tenant_record.slug,
                    "subject": issued.subject,
                    "role": issued.role.value,
                    "token": token,
                    "warning": (
                        "The token is shown only once."
                    ),
                }
            else:
                tenant_record = session.scalar(
                    select(Tenant).where(
                        Tenant.slug
                        == arguments.tenant_slug
                    )
                )

                if tenant_record is None:
                    raise RuntimeError(
                        "Tenant not found: "
                        f"{arguments.tenant_slug}"
                    )

                password = (
                    sys.stdin.readline().rstrip("\n")
                    if arguments.password_stdin
                    else getpass.getpass(
                        "Password (12+ characters): "
                    )
                )
                actor = (
                    authenticate_api_token(
                        session,
                        arguments.actor_token,
                    )
                    if arguments.actor_token
                    else None
                )
                created = create_password_identity(
                    session,
                    tenant=tenant_record,
                    subject=arguments.subject,
                    role=ApiRole(arguments.role),
                    password=password,
                    actor=actor,
                )
                result = {
                    "identity_id": str(created.id),
                    "tenant": tenant_record.slug,
                    "subject": created.subject,
                    "role": created.role.value,
                }
    except (
        PermissionError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"Error: {error}")
        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
