import logging
from datetime import datetime

from core.deps import PipelineDeps
from core.money import fmt_cents
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)

BASE_CONFIDENCE = 0.97
UNSIGNED_POD_PENALTY = 0.35
QTY_MISMATCH_PENALTY = 0.25
LOW_CLASSIFY_PENALTY = 0.10


def _finding(check: str, status: str, invalid: int, confidence: float, evidence: list[str], detail: str) -> dict:
    return {
        "check": check,
        "status": status,
        "amount_invalid_cents": invalid,
        "confidence": round(max(confidence, 0.0), 2),
        "evidence": evidence,
        "detail": detail,
    }


def _inconclusive(check: str, detail: str, evidence: list[str] | None = None) -> dict:
    return _finding(check, "inconclusive", 0, 0.0, evidence or [], detail)


def _line(fields: dict, sku: str | None) -> dict | None:
    return next((ln for ln in fields.get("lines", []) if ln.get("sku") == sku), None)


def _prorate(claimed: int, invalid_qty: int, claimed_qty: int) -> int:
    # share of the claimed dollars that belongs to the units found invalid, rounded half up
    return (claimed * invalid_qty + claimed_qty // 2) // claimed_qty


def _docs_by_type(documents: list[dict]) -> dict[str, dict]:
    by_type: dict[str, dict] = {}
    for doc in documents:
        by_type.setdefault(doc["doc_type"], doc)
    return by_type


def _classify_penalty(docs: list[dict]) -> float:
    return LOW_CLASSIFY_PENALTY if any(d["classify_stage"] == "llm" for d in docs) else 0.0


def check_required_documents(notice: dict, present: dict, required: list[str]) -> dict | None:
    missing = [t for t in required if t not in present]
    if missing:
        return _inconclusive(
            "required_documents",
            f"missing required documents for {notice.get('reason_code')}: {', '.join(missing)}",
        )
    return None


def check_reference_consistency(notice: dict, present: dict) -> dict | None:
    # catches the wrong document being pulled for a deduction, which would poison every later check
    problems = []
    for doc_type, doc in present.items():
        fields = doc["fields"]
        if doc_type != "deduction_notice" and fields.get("po_number") and notice.get("po_number"):
            if fields["po_number"] != notice["po_number"]:
                problems.append(f"{doc_type} references PO {fields['po_number']}, notice says {notice['po_number']}")
    inv = present.get("invoice")
    if inv and inv["fields"].get("invoice_number") and notice.get("invoice_number"):
        if inv["fields"]["invoice_number"] != notice["invoice_number"]:
            problems.append(f"invoice is {inv['fields']['invoice_number']}, notice says {notice['invoice_number']}")
    if problems:
        return _inconclusive("reference_consistency", "documents do not belong to the same order", problems)
    return None


def check_invoice_foots(present: dict) -> dict | None:
    # an invoice whose lines do not add up to its total has been damaged somewhere, so nothing on it can be trusted
    invoice = present.get("invoice")
    if not invoice:
        return None
    fields = invoice["fields"]
    lines = fields.get("lines", [])
    stated = fields.get("amount_due_cents")
    if stated is None or not lines or any(ln.get("qty") is None or ln.get("unit_price_cents") is None for ln in lines):
        return None
    computed = sum(ln["qty"] * ln["unit_price_cents"] for ln in lines)
    if computed != stated:
        return _inconclusive(
            "invoice_footing",
            "invoice lines do not add up to the stated amount due",
            [f"lines sum to {fmt_cents(computed)}, invoice states {fmt_cents(stated)}"],
        )
    return None


def check_amount_reconciles(notice: dict, present: dict) -> dict | None:
    # the deducted dollars should equal the claimed units at the invoice price, otherwise a human should look
    invoice = present.get("invoice")
    reason, sku, qty = notice.get("reason_code"), notice.get("sku"), notice.get("claimed_qty")
    if reason not in {"SHORT", "DMG", "PRICE"} or not invoice or not qty:
        return None
    line = _line(invoice["fields"], sku)
    if not line or not line.get("unit_price_cents"):
        return None
    if reason == "PRICE":
        alleged = notice.get("claimed_po_unit_price_cents")
        if alleged is None:
            return None
        expected = max(0, line["unit_price_cents"] - alleged) * qty
    else:
        expected = line["unit_price_cents"] * qty
    claimed = notice["claimed_amount_cents"]
    if expected != claimed:
        return _inconclusive(
            "amount_reconciliation",
            "claimed amount does not reconcile with claimed quantity at the invoice price",
            [f"{qty} units imply {fmt_cents(expected)}, notice deducts {fmt_cents(claimed)}"],
        )
    return None


def check_duplicate(case_id: int, notice: dict, deps: PipelineDeps) -> dict | None:
    original = deps.find_duplicate(case_id, notice)
    if not original:
        return None
    claimed = notice["claimed_amount_cents"]
    return _finding(
        "duplicate",
        "invalid_claim",
        claimed,
        BASE_CONFIDENCE,
        [f"deduction {original['deduction_ref']} dated {original['deduction_date']} already took the same amount for invoice {notice.get('invoice_number')}"],
        f"repeat deduction of {fmt_cents(claimed)} on the same invoice and reason code",
    )


def check_shortage(notice: dict, present: dict) -> dict:
    sku, claimed_qty, claimed = notice.get("sku"), notice.get("claimed_qty"), notice["claimed_amount_cents"]
    pod, bol = present["pod"], present["bol"]
    pod_line, bol_line = _line(pod["fields"], sku), _line(bol["fields"], sku)
    if not (claimed_qty and pod_line and bol_line):
        return _inconclusive("shortage", f"sku {sku} or claimed quantity not found on both pod and bol")
    shipped, received = bol_line.get("shipped_qty"), pod_line.get("received_qty")
    if shipped is None or received is None:
        return _inconclusive("shortage", f"quantities for {sku} unreadable on pod or bol")
    actual_short = max(0, shipped - received)
    invalid_qty = claimed_qty - min(claimed_qty, actual_short)
    invalid = _prorate(claimed, invalid_qty, claimed_qty)
    conf = BASE_CONFIDENCE - _classify_penalty([pod, bol])
    evidence = [
        f"bol {bol['fields'].get('bol_number')}: {shipped} units of {sku} shipped",
        f"pod {pod['fields'].get('pod_number')}: {received} units received, signed={pod['fields'].get('signed')}",
    ]
    if not pod["fields"].get("signed"):
        conf -= UNSIGNED_POD_PENALTY
        evidence.append("pod carries no receiver signature, so it is weak evidence")
    inv_line = _line(present["invoice"]["fields"], sku) if "invoice" in present else None
    if inv_line and inv_line.get("qty") != shipped:
        conf -= QTY_MISMATCH_PENALTY
        evidence.append(f"invoice bills {inv_line.get('qty')} units but bol shows {shipped}")
    status = "invalid_claim" if invalid > 0 else "valid_claim"
    detail = f"retailer claims {claimed_qty} short, records support {min(claimed_qty, actual_short)}"
    return _finding("shortage", status, invalid, conf, evidence, detail)


def check_damage(notice: dict, present: dict) -> dict:
    sku, claimed_qty, claimed = notice.get("sku"), notice.get("claimed_qty"), notice["claimed_amount_cents"]
    pod = present["pod"]
    pod_line = _line(pod["fields"], sku)
    if not (claimed_qty and pod_line):
        return _inconclusive("damage", f"sku {sku} or claimed quantity not found on the pod")
    damaged = pod_line.get("damaged_qty") or 0
    invalid_qty = claimed_qty - min(claimed_qty, damaged)
    invalid = _prorate(claimed, invalid_qty, claimed_qty)
    conf = BASE_CONFIDENCE - _classify_penalty([pod])
    evidence = [f"pod {pod['fields'].get('pod_number')}: {damaged} damaged units noted for {sku}, note: {pod_line.get('notes')}"]
    if not pod["fields"].get("signed"):
        conf -= UNSIGNED_POD_PENALTY
        evidence.append("pod carries no receiver signature, so it is weak evidence")
    status = "invalid_claim" if invalid > 0 else "valid_claim"
    return _finding("damage", status, invalid, conf, evidence, f"retailer claims {claimed_qty} damaged, pod supports {min(claimed_qty, damaged)}")


def check_pricing(notice: dict, present: dict) -> dict:
    sku, claimed = notice.get("sku"), notice["claimed_amount_cents"]
    inv_line = _line(present["invoice"]["fields"], sku)
    po_line = _line(present["po"]["fields"], sku)
    if not (inv_line and po_line and inv_line.get("unit_price_cents") and po_line.get("unit_price_cents")):
        return _inconclusive("pricing", f"sku {sku} not priced on both invoice and purchase order")
    qty = notice.get("claimed_qty") or inv_line.get("qty")
    if not qty:
        return _inconclusive("pricing", "no quantity to price the variance against")
    per_unit_owed = max(0, inv_line["unit_price_cents"] - po_line["unit_price_cents"])
    valid = min(claimed, per_unit_owed * qty)
    invalid = claimed - valid
    evidence = [
        f"po {present['po']['fields'].get('po_number')}: {sku} at {fmt_cents(po_line['unit_price_cents'])}",
        f"invoice {present['invoice']['fields'].get('invoice_number')}: {sku} at {fmt_cents(inv_line['unit_price_cents'])}",
    ]
    status = "invalid_claim" if invalid > 0 else "valid_claim"
    conf = BASE_CONFIDENCE - _classify_penalty([present["invoice"], present["po"]])
    return _finding("pricing", status, invalid, conf, evidence, f"{fmt_cents(valid)} of the variance is supported by the purchase order")


def check_delivery_window(notice: dict, present: dict) -> dict:
    claimed = notice["claimed_amount_cents"]
    pod, bol = present["pod"], present["bol"]
    delivered = pod["fields"].get("delivered_at")
    start, end = bol["fields"].get("appointment_start"), bol["fields"].get("appointment_end")
    if not (delivered and start and end):
        return _inconclusive("delivery_window", "delivery time or appointment window not readable")
    d, s, e = (datetime.fromisoformat(x) for x in (delivered, start, end))
    on_time = s <= d <= e
    evidence = [f"appointment window {start} to {end}", f"pod delivered at {delivered}"]
    conf = BASE_CONFIDENCE - _classify_penalty([pod, bol])
    if on_time:
        return _finding("delivery_window", "invalid_claim", claimed, conf, evidence, "delivery landed inside the appointment window")
    return _finding("delivery_window", "valid_claim", 0, conf, evidence, "delivery landed outside the appointment window")


REASON_CHECKS = {
    "SHORT": check_shortage,
    "DMG": check_damage,
    "PRICE": check_pricing,
    "OTIF": check_delivery_window,
}


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    notice = state.get("notice") or {}
    if not all(notice.get(k) for k in ("deduction_ref", "reason_code", "claimed_amount_cents")):
        finding = _inconclusive("notice", "deduction notice missing or unreadable")
        return {"findings": [finding], "audit": [audit_event("validate", "validation_skipped", finding["detail"])]}

    present = _docs_by_type(state.get("documents", []))
    reason = notice["reason_code"]
    findings: list[dict] = []

    dup = check_duplicate(state["case_id"], notice, deps)
    if dup:
        findings.append(dup)

    missing = check_required_documents(notice, present, state.get("policy", {}).get("required_docs", []))
    if missing:
        findings.append(missing)
    elif reason in REASON_CHECKS:
        consistency = check_reference_consistency(notice, present)
        if consistency:
            findings.append(consistency)
        else:
            findings.append(REASON_CHECKS[reason](notice, present))
            findings.extend(f for f in (check_invoice_foots(present), check_amount_reconciles(notice, present)) if f)
    else:
        findings.append(_inconclusive(reason.lower(), f"{reason} deductions need a trade agreement that is not in the retrieved documents"))

    summary = "; ".join(f"{f['check']}={f['status']} ({fmt_cents(f['amount_invalid_cents'])} invalid)" for f in findings)
    logger.info(f"case {state['case_id']}: {summary}")
    return {"findings": findings, "audit": [audit_event("validate", "checks_run", summary, findings=findings)]}
