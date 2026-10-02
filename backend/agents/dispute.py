import logging

from core.deps import PipelineDeps
from core.money import fmt_cents
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)

LETTER_SYSTEM = (
    "you write concise, professional deduction dispute letters from a consumer goods supplier to a retailer. "
    "use only the facts provided. do not invent numbers, dates or document names. no markdown, no emojis."
)

REASON_QUERY = {
    "SHORT": "shortage claims proof of delivery bill of lading quantity received",
    "DMG": "damage claims noted on proof of delivery at receipt",
    "PRICE": "pricing claims purchase order unit price invoice",
    "OTIF": "delivery window appointment late delivery compliance",
}


def _evidence_lines(findings: list[dict]) -> list[str]:
    lines = []
    for f in findings:
        if f["amount_invalid_cents"] > 0:
            lines.extend(f["evidence"])
    return lines


def _template_letter(ctx: dict) -> str:
    evidence = "\n".join(f"- {line}" for line in ctx["evidence"]) or "- see attached documents"
    citations = "\n".join(f"- {c['display_name']}, section {c['section']}" for c in ctx["citations"]) or "- retailer deduction policy"
    attachments = "\n".join(f"- {name}" for name in ctx["attachments"])
    return (
        f"To: {ctx['display_name']} Deductions Team\n"
        f"Date: {ctx['today']}\n"
        f"Re: Dispute of deduction {ctx['deduction_ref']} against invoice {ctx['invoice_number']}, PO {ctx['po_number']}\n\n"
        f"We are disputing deduction {ctx['deduction_ref']} dated {ctx['deduction_date']} "
        f"({ctx['reason_code']}, {fmt_cents(ctx['claimed_cents'])} deducted). "
        f"Our records show {fmt_cents(ctx['amount_cents'])} of this amount is not owed.\n\n"
        f"Basis for the dispute:\n{ctx['basis']}\n\n"
        f"Supporting evidence:\n{evidence}\n\n"
        f"Applicable policy:\n{citations}\n\n"
        f"Attached documents:\n{attachments}\n\n"
        f"We request a credit of {fmt_cents(ctx['amount_cents'])}. This dispute is submitted within the "
        f"dispute window that closes {ctx['deadline']}.\n"
    )


def draft(state: PipelineState, deps: PipelineDeps, amount_cents: int | None = None) -> dict:
    notice, policy = state["notice"], state["policy"]
    findings = state["findings"]
    invalid = amount_cents if amount_cents is not None else state["decision"]["invalid_cents"]
    retailer = state["retailer"]
    reason = notice["reason_code"]

    hits = deps.policies.search(REASON_QUERY.get(reason, "deduction dispute") + " dispute window", retailer, k=2)
    window = deps.policies.search("dispute window", retailer, k=1)
    chunks = {c.section: c for c in [*hits, *window]}.values()
    citations = [{"display_name": policy["display_name"], "section": c.section, "source": c.source} for c in chunks]

    ctx = {
        "display_name": policy["display_name"],
        "today": deps.today().isoformat(),
        "deduction_ref": notice["deduction_ref"],
        "invoice_number": notice.get("invoice_number", "n/a"),
        "po_number": notice.get("po_number", "n/a"),
        "deduction_date": notice.get("deduction_date"),
        "reason_code": reason,
        "claimed_cents": notice["claimed_amount_cents"],
        "amount_cents": invalid,
        "basis": "; ".join(f["detail"] for f in findings if f["amount_invalid_cents"] > 0) or "see evidence",
        "evidence": _evidence_lines(findings),
        "citations": citations,
        "attachments": [d["filename"] for d in state["documents"] if d["doc_type"] != "unknown"],
        "deadline": policy.get("deadline") or "n/a",
    }
    letter = _template_letter(ctx)
    if deps.llm.is_live:
        polished = deps.llm.complete(LETTER_SYSTEM, f"write the dispute letter from these facts:\n{ctx}", max_tokens=900)
        # reject any rewrite that drops the reference or the requested amount
        if polished.strip() and ctx["deduction_ref"] in polished and fmt_cents(invalid) in polished:
            letter = polished.strip()
        else:
            logger.warning(f"llm letter for {ctx['deduction_ref']} failed the fact check, using template")
    return {
        "amount_cents": invalid,
        "letter": letter,
        "citations": citations,
        "attachments": ctx["attachments"],
        "deadline": policy.get("deadline"),
    }


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    if state["decision"]["decision"] != "DISPUTE":
        return {"dispute": None}
    dispute = draft(state, deps)
    reasoning = f"drafted dispute for {fmt_cents(dispute['amount_cents'])} citing {len(dispute['citations'])} policy sections"
    logger.info(f"case {state['case_id']}: {reasoning}")
    return {"dispute": dispute, "audit": [audit_event("dispute", "dispute_drafted", reasoning, amount_cents=dispute["amount_cents"])]}
