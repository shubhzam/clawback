import logging
import re
from collections import Counter

from core.deps import PipelineDeps
from core.llm import complete_json
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)

DOC_TYPES = ["deduction_notice", "invoice", "pod", "bol", "po", "remittance"]

# stage 1: the document title on one of its first lines
TITLE_PATTERNS = {
    "deduction_notice": re.compile(r"^\s*(deduction (notice|detail)|chargeback notice)", re.I),
    "invoice": re.compile(r"^\s*(commercial )?invoice\s*$", re.I),
    "pod": re.compile(r"^\s*proof of delivery", re.I),
    "bol": re.compile(r"^\s*bill of lading", re.I),
    "po": re.compile(r"^\s*purchase order\s*$", re.I),
    "remittance": re.compile(r"^\s*(remittance advice|payment advice)", re.I),
}

# stage 2: distinctive phrases anywhere in the body
KEYWORDS = {
    "deduction_notice": ["deduction", "reason code", "claim amount", "claimed", "chargeback", "short pay"],
    "invoice": ["invoice number", "invoice no", "unit price", "bill to", "terms", "amount due"],
    "pod": ["received by", "delivered at", "consignee", "signature", "receiving", "signed"],
    "bol": ["carrier", "scac", "pallets", "appointment", "shipper", "shipped"],
    "po": ["purchase order", "buyer", "ordered", "ship window", "po number"],
    "remittance": ["remittance", "payment advice", "check number", "payment date", "net paid"],
}

LLM_SYSTEM = (
    "you classify retail supply chain documents. reply with json only: "
    '{"doc_type": one of ' + str(DOC_TYPES) + ' or "unknown", "confidence": number between 0 and 1}'
)


def _title_match(text: str) -> str | None:
    lines = [ln for ln in text.splitlines() if ln.strip()][:5]
    for doc_type, pattern in TITLE_PATTERNS.items():
        if any(pattern.match(ln) for ln in lines):
            return doc_type
    return None


def _keyword_match(text: str) -> tuple[str | None, float]:
    lowered = text.lower()
    scores = Counter({t: sum(1 for kw in kws if kw in lowered) for t, kws in KEYWORDS.items()})
    ranked = scores.most_common(2)
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_score >= 3 and best_score > runner_up:
        return best, min(0.9, 0.5 + 0.08 * best_score)
    return None, 0.0


def classify_document(text: str, deps: PipelineDeps) -> tuple[str, str, float]:
    # returns (doc_type, stage, confidence). waterfall stops at the first stage that answers
    if not text.strip():
        return "unknown", "none", 0.0
    by_title = _title_match(text)
    if by_title:
        return by_title, "regex", 0.99
    by_keyword, conf = _keyword_match(text)
    if by_keyword:
        return by_keyword, "keywords", conf
    if deps.llm.is_live:
        reply = complete_json(deps.llm, LLM_SYSTEM, text[:3000], max_tokens=100)
        if reply and reply.get("doc_type") in DOC_TYPES:
            return reply["doc_type"], "llm", min(float(reply.get("confidence", 0.6)), 0.85)
    return "unknown", "none", 0.0


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    documents = []
    stages: Counter = Counter()
    for doc in state.get("documents", []):
        doc_type, stage, conf = classify_document(doc.get("text", ""), deps)
        stages[stage] += 1
        documents.append({**doc, "doc_type": doc_type, "classify_stage": stage, "classify_confidence": conf})
    types = Counter(d["doc_type"] for d in documents)
    reasoning = "classified " + ", ".join(f"{n} {t}" for t, n in sorted(types.items())) if documents else "no documents to classify"
    logger.info(f"case {state['case_id']}: {reasoning}, stages={dict(stages)}")
    return {"documents": documents, "audit": [audit_event("classify", "documents_classified", reasoning, stages=dict(stages))]}
