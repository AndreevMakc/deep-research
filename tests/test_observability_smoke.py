import uuid

from app.db.models import (
    EventStatus,
    ResearchRun,
    ReviewerIdentity,
    ReviewerRole,
)
from app.db.repositories import (
    create_research_run,
    get_operational_events_for_run,
)
from app.db.session import SessionFactory
from app.observability import (
    emit_event,
    observability_context,
)
from app.rbac import authorize


def main() -> None:
    suffix = uuid.uuid4().hex[:12]
    subject = f"observability-smoke-{suffix}"

    with SessionFactory() as session:
        run = create_research_run(
            session,
            "Observability smoke",
        )
        run_id = run.id
        identity = ReviewerIdentity(
            subject=subject,
            display_name="Observability Smoke",
            role=ReviewerRole.VIEWER,
            active=True,
        )
        session.add(identity)
        session.commit()
        identity_id = identity.id
        authorized = authorize(
            session,
            subject,
            "view",
        )
        assert authorized.id == identity_id

    with observability_context(
        run_id=run_id,
        agent="smoke-agent",
        correlation_id=str(run_id),
    ):
        emit_event(
            operation="smoke.operation",
            event_type="smoke",
            status=EventStatus.SUCCEEDED,
            duration_ms=12.5,
            token_estimate=42,
            estimated_cost_usd=0.001,
            metadata={
                "api_key": "must-not-persist",
                "safe": "value",
            },
        )

    with SessionFactory() as session:
        events = get_operational_events_for_run(
            session,
            run_id,
        )
        assert len(events) == 1
        event = events[0]
        assert event.correlation_id == str(run_id)
        assert event.agent == "smoke-agent"
        assert event.token_estimate == 42
        assert event.metadata_json["api_key"] == "[REDACTED]"
        assert event.metadata_json["safe"] == "value"

        run = session.get(ResearchRun, run_id)
        identity = session.get(
            ReviewerIdentity,
            identity_id,
        )
        assert run is not None
        assert identity is not None
        session.delete(run)
        session.delete(identity)
        session.commit()

    print("Observability and RBAC smoke test OK")


if __name__ == "__main__":
    main()
