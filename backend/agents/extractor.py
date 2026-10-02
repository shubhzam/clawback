import logging
import re
from datetime import date, datetime

from core.deps import PipelineDeps
from core.llm import complete_json
from core.money import to_cents
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)

REASON_ALIASES = {
    "SHORT": "SHORT", "SHORTAGE": "SHORT", "SHORT_PAY": "SHORT",
    "DMG": "DMG", "DAMAGE": "DMG", "DAMAGED": "DMG",
    "PRICE": "PRICE", "PRICING": "PRICE", "PRICE_VARIANCE": "PRICE",
    "OTIF": "OTIF", "LATE": "OTIF", "LATE_DELIVERY": "OTIF",
    "PROMO": "PROMO", "TRADE": "PROMO", "PROMOTION": "PROMO",
}

# portals label the same field in different ways, so labels are normalised through this map
KEY_ALIASES = {
    "claim_amount": "claimed_amount", "deduction_amount": "claimed_amount", "amount_claimed": "claimed_amount",
    "inv_no": "invoice_number", "invoice_no": "invoice_number", "invoice": "invoice_number",
    "po": "po_number", "po_no": "po_number", "purchase_order": "po_number",
    "ref": "deduction_ref", "deduction_reference": "deduction_ref", "deduction_id": "deduction_ref",
    "reason": "reason_code", "code": "reason_code",
    "date": "deduction_date", "claim_date": "deduction_date",
    "qty_claimed": "claimed_qty", "claim_qty": "claimed_qty",
    "received_on": "delivered_at", "delivery_time": "delivered_at",
    "signed_by_receiver": "signed",
    "window_start": "appointment_start", "window_end": "appointment_end",
}

LINE_COLUMNS = {
    "invoice": ["sku", "description", "qty", "unit_price_cents"],
    "pod": ["sku", "received_qty", "damaged_qty", "notes"],
    "bol": ["sku", "shipped_qty", "pallets"],
    "po": ["sku", "ordered_qty", "unit_price_cents"],
}


def _text(v: str) -> str:
    return v.strip()


def _int(v: str) -> int | None:
    m = re.search(r"-?\d[\d,]*", v)
    return int(m.group(0).replace(",", "")) if m else None


def _date(v: str) -> str | None:
    try:
        return date.fromisoformat(v.strip()[:10]).isoformat()
    except ValueError:
        return None


def _datetime(v: str) -> str | None:
    try:
        return datetime.fromisoformat(v.strip()).isoformat()
    except ValueError:
        return None


def _bool(v: str) -> bool:
    return v.strip().lower() in {"yes", "y", "true", "signed"}


def _reason(v: str) -> str:
    key = re.sub(r"[^A-Z]+", "_", v.upper()).strip("_")
    return REASON_ALIASES.get(key, key)


SCALARS = {
    "deduction_notice": {
        "deduction_ref": _text, "reason_code": _reason, "claimed_amount_cents": to_cents,
        "deduction_date": _date, "invoice_number": _text, "po_number": _text, "sku": _text,
        "claimed_qty": _int, "claimed_po_unit_price_cents": to_cents,
    },
    "invoice": {"invoice_number": _text, "po_number": _text, "ship_date": _date, "amount_due_cents": to_cents},
    "pod": {"pod_number": _text, "po_number": _text, "delivered_at": _datetime, "received_by": _text, "signed": _bool},
    "bol": {
        "bol_number": _text, "po_number": _text, "ship_date": _date,
        "appointment_start": _datetime, "appointment_end": _datetime, "carrier": _text,
    },
    "po": {"po_number": _text, "buyer": _text},
    "remittance": {"remittance_number": _text, "payment_date": _date, "net_paid_amount_cents": to_cents},
}

REQUIRED = {
    "deduction_notice": ["deduction_ref", "reason_code", "claimed_amount_cents", "deduction_date"],
}

# the notice carries the amount under claimed_amount, mapped to the cents field here
FIELD_LABELS = {"claimed_amount": "claimed_amount_cents", "claimed_po_unit_price": "claimed_po_unit_price_cents",
                "net_paid_amount": "net_paid_amount_cents", "amount_due": "amount_due_cents"}


def _norm_key(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    key = KEY_ALIASES.get(key, key)
    return FIELD_LABELS.get(key, key)


def _parse_lines(doc_type: str, lines: list[list[str]]) -> list[dict]:
    columns = LINE_COLUMNS.get(doc_type, [])
    parsed = []
    for cells in lines:
        row: dict = {}
        for col, cell in zip(columns, cells):
            if col.endswith("_cents"):
                row[col] = to_cents(cell)
            elif col.endswith("_qty") or col in {"qty", "pallets"}:
                row[col] = _int(cell)
            else:
                row[col] = cell
        parsed.append(row)
    return parsed


def parse_document(doc_type: str, text: str) -> dict:
    # deterministic label: value parser, covers the structured exports most portals produce
    scalars = SCALARS.get(doc_type, {})
    fields: dict = {}
    raw_lines: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("LINE |"):
            raw_lines.append([c.strip() for c in stripped.split("|")[1:]])
            continue
        if ":" not in stripped:
            continue
        label, _, value = stripped.partition(":")
        key = _norm_key(label)
        if key in scalars and key not in fields:
            parsed = scalars[key](value)
            if parsed is not None:
                fields[key] = parsed
    if doc_type in LINE_COLUMNS:
        fields["lines"] = _parse_lines(doc_type, raw_lines)
    return fields


def _llm_fill(doc_type: str, text: str, missing: list[str], deps: PipelineDeps) -> dict:
    system = (
        "extract fields from a retail document. reply with json only containing exactly the requested keys. "
        "use null for anything not present. amounts as plain decimal strings, dates as YYYY-MM-DD. never guess."
    )
    reply = complete_json(deps.llm, system, f"document type: {doc_type}\nkeys: {missing}\n\n{text[:4000]}")
    if not reply:
        return {}
    filled = {}
    for key in missing:
        raw = reply.get(key)
        if raw in (None, ""):
            continue
        value = SCALARS[doc_type][key](str(raw))
        if value is not None:
            filled[key] = value
    return filled


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    documents = []
    inferred_total = 0
    for doc in state.get("documents", []):
        doc_type = doc.get("doc_type", "unknown")
        fields = parse_document(doc_type, doc.get("text", "")) if doc_type != "unknown" else {}
        required = REQUIRED.get(doc_type, [])
        missing = [k for k in required if k not in fields]
        if missing and deps.llm.is_live:
            filled = _llm_fill(doc_type, doc["text"], missing, deps)
            if filled:
                fields.update(filled)
                fields["_inferred"] = sorted(filled)
                inferred_total += len(filled)
        if "reason_code" in fields:
            fields["reason_code"] = _reason(fields["reason_code"])
        documents.append({**doc, "fields": fields})

    notices = [d for d in documents if d["doc_type"] == "deduction_notice"]
    notice = dict(notices[0]["fields"]) if notices else {}
    missing_notice = [k for k in REQUIRED["deduction_notice"] if k not in notice]
    if not notices:
        reasoning = "no deduction notice found among the retrieved documents"
    elif missing_notice:
        reasoning = f"deduction notice is missing fields: {', '.join(missing_notice)}"
    else:
        reasoning = f"extracted notice {notice['deduction_ref']} with {inferred_total} llm-inferred fields"
    logger.info(f"case {state['case_id']}: {reasoning}")
    return {
        "documents": documents,
        "notice": notice,
        "audit": [audit_event("extract", "fields_extracted", reasoning, missing=missing_notice, inferred=inferred_total)],
    }
