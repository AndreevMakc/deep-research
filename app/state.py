from typing import Annotated, TypedDict


def merge_unique(
    current: list[str],
    update: list[str],
) -> list[str]:
    return list(dict.fromkeys([*current, *update]))


def merge_findings(
    current: list[dict],
    update: list[dict],
) -> list[dict]:
    by_task = {
        item["task_id"]: item
        for item in current
    }

    for item in update:
        by_task[item["task_id"]] = item

    return list(by_task.values())


def merge_verifications(
    current: list[dict],
    update: list[dict],
) -> list[dict]:
    by_claim = {
        item["claim_id"]: item
        for item in current
    }

    for item in update:
        by_claim[item["claim_id"]] = item

    return list(by_claim.values())


class ResearchState(TypedDict):
    """Shared state for one research run."""

    run_id: str
    question: str
    research_input: dict
    plan: dict
    task_ids: list[str]
    findings: Annotated[list[dict], merge_findings]
    claim_ids: Annotated[list[str], merge_unique]
    pending_claim_ids: list[str]
    verifications: Annotated[
        list[dict],
        merge_verifications,
    ]
    report: str
    report_json: dict


class ResearchWorkerState(TypedDict):
    """Input and reducer output for one Researcher worker."""

    run_id: str
    task_id: str
    findings: Annotated[list[dict], merge_findings]


class VerificationWorkerState(TypedDict):
    """Input and output for one Verifier worker."""

    run_id: str
    claim_id: str
    verifications: Annotated[
        list[dict],
        merge_verifications,
    ]
