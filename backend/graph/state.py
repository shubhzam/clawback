import operator
from datetime import datetime, timezone
from typing import Annotated, TypedDict


class PipelineState(TypedDict, total=False):
    case_id: int
    retailer: str
    deduction_ref: str
    documents: list[dict]
    notice: dict
    policy: dict
    findings: list[dict]
    decision: dict
    dispute: dict | None
    # audit events from every node are concatenated, never overwritten
    audit: Annotated[list[dict], operator.add]


def audit_event(agent: str, action: str, reasoning: str = "", **payload) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": action,
        "reasoning": reasoning,
        "payload": payload,
    }
