import logging

from core.deps import PipelineDeps
from core.money import fmt_cents
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)


def decide(notice: dict, findings: list[dict], policy: dict, deps: PipelineDeps) -> dict:
    # ordered rules, first match wins. a deterministic rule trail keeps every decision explainable
    settings = deps.settings
    claimed = notice.get("claimed_amount_cents") or 0
    # all checks look at the same claimed dollars, so the invalid amount is the largest finding, not the sum
    invalid = min(claimed, max((f["amount_invalid_cents"] for f in findings), default=0))
    confidences = [f["confidence"] for f in findings if f["status"] != "inconclusive"]
    confidence = min(confidences) if confidences else 0.0
    base = {"invalid_cents": invalid, "valid_cents": claimed - invalid, "confidence": round(confidence, 2)}

    inconclusive = [f for f in findings if f["status"] == "inconclusive"]
    days_remaining = policy.get("days_remaining")

    if inconclusive:
        reasons = [f"{f['check']}: {f['detail']}" for f in inconclusive]
        return {**base, "decision": "ESCALATE", "rule": "inconclusive_evidence", "reasons": reasons}
    if invalid == 0:
        return {**base, "decision": "ACCEPT", "rule": "claim_supported", "reasons": ["records support the full deduction"]}
    if days_remaining is not None and days_remaining < 0:
        return {
            **base, "decision": "ESCALATE", "rule": "window_closed",
            "reasons": [f"{fmt_cents(invalid)} looks invalid but the dispute window closed {policy['deadline']}"],
        }
    if confidence < settings.min_dispute_confidence:
        return {
            **base, "decision": "ESCALATE", "rule": "low_confidence",
            "reasons": [f"confidence {confidence:.2f} is below the {settings.min_dispute_confidence:.2f} auto-dispute floor"],
        }
    if invalid > settings.auto_dispute_max_cents:
        return {
            **base, "decision": "ESCALATE", "rule": "high_value_signoff",
            "reasons": [f"{fmt_cents(invalid)} exceeds the {fmt_cents(settings.auto_dispute_max_cents)} auto-dispute limit"],
        }
    reasons = [f["detail"] for f in findings if f["amount_invalid_cents"] > 0]
    return {**base, "decision": "DISPUTE", "rule": "invalid_deduction", "reasons": reasons}


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    decision = decide(state.get("notice") or {}, state.get("findings", []), state.get("policy", {}), deps)
    reasoning = f"{decision['decision']} via {decision['rule']}: " + "; ".join(decision["reasons"])
    logger.info(f"case {state['case_id']}: {reasoning}")
    return {"decision": decision, "audit": [audit_event("orchestrate", "decision_made", reasoning, **decision)]}
