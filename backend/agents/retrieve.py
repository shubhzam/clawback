import logging
from pathlib import Path

from core.deps import PipelineDeps
from core.ocr import extract_text
from graph.state import PipelineState, audit_event

logger = logging.getLogger(__name__)


def run(state: PipelineState, deps: PipelineDeps) -> dict:
    # pulls every document the portal holds for this deduction and reads it into text
    retailer, ref = state["retailer"], state["deduction_ref"]
    portal_docs = deps.portal.fetch_documents(retailer, ref)
    documents = []
    for doc in portal_docs:
        ocr = extract_text(Path(doc.path))
        documents.append({"filename": doc.filename, "text": ocr.text, "ocr_method": ocr.method})
    logger.info(f"retrieved {len(documents)} documents for {retailer}/{ref}")
    unreadable = [d["filename"] for d in documents if not d["text"].strip()]
    reasoning = f"pulled {len(documents)} documents from the {retailer} portal"
    if unreadable:
        reasoning += f", {len(unreadable)} unreadable: {', '.join(unreadable)}"
    return {
        "documents": documents,
        "audit": [audit_event("retrieve", "documents_retrieved", reasoning, count=len(documents), unreadable=unreadable)],
    }
