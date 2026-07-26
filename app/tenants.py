from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.db.models import ApiRole, Tenant
from app.db.session import SessionFactory
from app.multitenancy import (
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
