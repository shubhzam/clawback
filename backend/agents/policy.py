import logging
from datetime import date, timedelta

from core.deps import PipelineDeps
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    # resolves the retailer rules that apply to this deduction: dispute window and required evidence
    retailer = state["retailer"]
    notice = state.get("notice") or {}
    meta = deps.policies.meta(retailer)
    reason = notice.get("reason_code")
    required = meta["required_docs"].get(reason, []) if reason else []
    window_days = int(meta["dispute_window_days"])

    deduction_date = date.fromisoformat(notice["deduction_date"]) if notice.get("deduction_date") else None
    deadline = deduction_date + timedelta(days=window_days) if deduction_date else None
    days_remaining = (deadline - deps.today()).days if deadline else None

    policy = {
        "retailer": retailer,
        "display_name": meta["display_name"],
        "known_retailer": deps.policies.known(retailer),
        "window_days": window_days,
        "deadline": deadline.isoformat() if deadline else None,
        "days_remaining": days_remaining,
        "required_docs": required,
    }
    if not policy["known_retailer"]:
        reasoning = f"no policy on file for {retailer}, using the default {window_days} day window"
    elif days_remaining is None:
        reasoning = f"{meta['display_name']} allows {window_days} days but the deduction date is unknown"
    elif days_remaining >= 0:
        reasoning = f"{meta['display_name']} dispute window closes {deadline.isoformat()}, {days_remaining} days left"
    else:
        reasoning = f"{meta['display_name']} dispute window closed {deadline.isoformat()}, {-days_remaining} days ago"
    logger.info(f"case {state['case_id']}: {reasoning}")
    return {"policy": policy, "audit": [audit_event("policy", "policy_resolved", reasoning, **policy)]}
