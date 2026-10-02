import logging
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import AuditEvent, Case, Dispute, Document

logger = logging.getLogger(__name__)

DECISION_STATUS = {"DISPUTE": "DISPUTE_DRAFTED", "ACCEPT": "ACCEPTED", "ESCALATE": "ESCALATED"}
LOCKED_DISPUTE_STATES = {"FILED", "WON", "PARTIAL", "LOST"}
REPROCESSABLE = {"PENDING", "DISPUTE_DRAFTED", "ACCEPTED", "ESCALATED", "ERROR"}


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def get_or_create_case(session: Session, retailer: str, deduction_ref: str) -> tuple[Case, bool]:
    case = session.scalar(select(Case).where(Case.retailer == retailer, Case.deduction_ref == deduction_ref))
    if case:
        return case, False
    case = Case(retailer=retailer, deduction_ref=deduction_ref, state_json={})
    session.add(case)
    session.flush()
    return case, True


def add_audit(session: Session, case_id: int, agent: str, action: str, reasoning: str = "", **payload) -> None:
    session.add(AuditEvent(case_id=case_id, agent=agent, action=action, reasoning=reasoning, payload=payload))


def find_duplicate(session: Session, case_id: int, notice: dict) -> dict | None:
    # a deduction is a duplicate if an earlier one (by date, then id) hits the same invoice, reason and amount
    case = session.get(Case, case_id)
    this_date = _parse_date(notice.get("deduction_date"))
    invoice = notice.get("invoice_number")
    reason = notice.get("reason_code")
    claimed = notice.get("claimed_amount_cents")
    if not (case and this_date and invoice and reason and claimed):
        return None
    others = session.scalars(
        select(Case).where(
            Case.retailer == case.retailer,
            Case.id != case_id,
            Case.deduction_ref != case.deduction_ref,
            Case.invoice_number == invoice,
            Case.reason_code == reason,
            Case.claimed_cents == claimed,
        )
    ).all()
    earlier = [o for o in others if o.deduction_date and (o.deduction_date, o.id) < (this_date, case_id)]
    if not earlier:
        return None
    first = min(earlier, key=lambda o: (o.deduction_date, o.id))
    return {"deduction_ref": first.deduction_ref, "deduction_date": first.deduction_date.isoformat(), "case_id": first.id}


def later_duplicates_to_requeue(session: Session, case: Case) -> list[int]:
    # when an earlier deduction lands after a later twin was already processed, the twin needs a rerun
    if not (case.invoice_number and case.reason_code and case.deduction_date):
        return []
    twins = session.scalars(
        select(Case).where(
            Case.retailer == case.retailer,
            Case.id != case.id,
            Case.invoice_number == case.invoice_number,
            Case.reason_code == case.reason_code,
            Case.claimed_cents == case.claimed_cents,
        )
    ).all()
    out = []
    for twin in twins:
        if not twin.deduction_date or (twin.deduction_date, twin.id) <= (case.deduction_date, case.id):
            continue
        flagged = any(f.get("check") == "duplicate" for f in twin.state_json.get("findings", []))
        if not flagged and twin.status in REPROCESSABLE and twin.status != "PENDING":
            out.append(twin.id)
    return out


def save_result(session: Session, case: Case, state: dict) -> None:
    notice = state.get("notice") or {}
    decision = state.get("decision") or {}
    policy = state.get("policy") or {}

    case.reason_code = notice.get("reason_code")
    case.invoice_number = notice.get("invoice_number")
    case.deduction_date = _parse_date(notice.get("deduction_date"))
    case.window_deadline = _parse_date(policy.get("deadline"))
    case.claimed_cents = notice.get("claimed_amount_cents") or 0
    case.invalid_cents = decision.get("invalid_cents", 0)
    case.confidence = decision.get("confidence")
    case.decision = decision.get("decision")
    case.summary = "; ".join(decision.get("reasons", []))
    case.state_json = {
        "notice": notice,
        "findings": state.get("findings", []),
        "policy": policy,
        "decision": decision,
    }

    case.documents.clear()
    session.flush()
    for doc in state.get("documents", []):
        case.documents.append(
            Document(
                filename=doc["filename"],
                doc_type=doc.get("doc_type", "unknown"),
                classify_stage=doc.get("classify_stage", "none"),
                classify_confidence=doc.get("classify_confidence", 0.0),
                ocr_method=doc.get("ocr_method", "none"),
                text=doc.get("text", ""),
                fields=doc.get("fields", {}),
            )
        )

    for event in state.get("audit", []):
        session.add(
            AuditEvent(
                case_id=case.id,
                ts=datetime.fromisoformat(event["ts"]),
                agent=event["agent"],
                action=event["action"],
                reasoning=event.get("reasoning", ""),
                payload=event.get("payload", {}),
            )
        )

    locked = case.dispute is not None and case.dispute.status in LOCKED_DISPUTE_STATES
    if locked:
        return

    new_dispute = state.get("dispute")
    if new_dispute and decision.get("decision") == "DISPUTE":
        upsert_dispute(case, new_dispute)
    elif case.dispute is not None:
        # a stale draft from an earlier run no longer matches the decision
        session.delete(case.dispute)
        case.dispute = None
    case.status = DECISION_STATUS.get(decision.get("decision"), "ESCALATED")


def upsert_dispute(case: Case, data: dict) -> None:
    if case.dispute is None:
        case.dispute = Dispute()
    case.dispute.status = "DRAFTED"
    case.dispute.amount_cents = data["amount_cents"]
    case.dispute.letter = data["letter"]
    case.dispute.citations = data["citations"]
    case.dispute.attachments = data["attachments"]
    case.dispute.deadline = _parse_date(data.get("deadline"))


def case_summary(case: Case) -> dict:
    return {
        "id": case.id,
        "retailer": case.retailer,
        "deduction_ref": case.deduction_ref,
        "status": case.status,
        "decision": case.decision,
        "reason_code": case.reason_code,
        "invoice_number": case.invoice_number,
        "claimed_cents": case.claimed_cents,
        "invalid_cents": case.invalid_cents,
        "recovered_cents": case.recovered_cents,
        "confidence": case.confidence,
        "deduction_date": case.deduction_date.isoformat() if case.deduction_date else None,
        "window_deadline": case.window_deadline.isoformat() if case.window_deadline else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def case_detail(case: Case) -> dict:
    dispute = case.dispute
    return {
        **case_summary(case),
        "summary": case.summary,
        "notice": case.state_json.get("notice", {}),
        "findings": case.state_json.get("findings", []),
        "policy": case.state_json.get("policy", {}),
        "decision_detail": case.state_json.get("decision", {}),
        "documents": [
            {
                "filename": d.filename,
                "doc_type": d.doc_type,
                "classify_stage": d.classify_stage,
                "classify_confidence": d.classify_confidence,
                "ocr_method": d.ocr_method,
                "fields": d.fields,
            }
            for d in case.documents
        ],
        "audit": [
            {
                "ts": e.ts.isoformat(),
                "agent": e.agent,
                "action": e.action,
                "reasoning": e.reasoning,
                "payload": e.payload,
            }
            for e in case.audit_events
        ],
        "dispute": None
        if dispute is None
        else {
            "status": dispute.status,
            "amount_cents": dispute.amount_cents,
            "letter": dispute.letter,
            "citations": dispute.citations,
            "attachments": dispute.attachments,
            "deadline": dispute.deadline.isoformat() if dispute.deadline else None,
            "confirmation_id": dispute.confirmation_id,
            "erp_memo_id": dispute.erp_memo_id,
            "filed_at": dispute.filed_at.isoformat() if dispute.filed_at else None,
        },
    }


def compute_stats(session: Session) -> dict:
    cases = session.scalars(select(Case)).all()
    by_status: dict[str, int] = defaultdict(int)
    by_retailer: dict[str, dict] = defaultdict(lambda: {"cases": 0, "claimed_cents": 0, "invalid_cents": 0, "recovered_cents": 0})
    for c in cases:
        by_status[c.status] += 1
        row = by_retailer[c.retailer]
        row["cases"] += 1
        row["claimed_cents"] += c.claimed_cents
        row["invalid_cents"] += c.invalid_cents if c.decision == "DISPUTE" else 0
        row["recovered_cents"] += c.recovered_cents

    disputes = session.scalars(select(Dispute)).all()
    resolved = [d for d in disputes if d.status in {"WON", "PARTIAL", "LOST"}]
    won = [d for d in resolved if d.status in {"WON", "PARTIAL"}]
    resolved_amount = sum(d.amount_cents for d in resolved)
    recovered = sum(c.recovered_cents for c in cases)
    return {
        "total_cases": len(cases),
        "by_status": dict(by_status),
        "claimed_cents": sum(c.claimed_cents for c in cases),
        "flagged_invalid_cents": sum(c.invalid_cents for c in cases if c.decision == "DISPUTE"),
        "recovered_cents": recovered,
        "awaiting_review": by_status.get("ESCALATED", 0),
        "disputes_resolved": len(resolved),
        "dispute_win_rate": (len(won) / len(resolved)) if resolved else None,
        "dollar_recovery_rate": (recovered / resolved_amount) if resolved_amount else None,
        "by_retailer": dict(by_retailer),
    }


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
