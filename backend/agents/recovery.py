import logging

from core.erp import MockERP

logger = logging.getLogger(__name__)

OUTCOME_STATUS = {"won": "WON", "partial": "PARTIAL", "lost": "LOST"}


def apply_outcome(retailer: str, deduction_ref: str, disputed_cents: int, outcome: str, recovered_cents: int, erp: MockERP) -> dict:
    # records what the retailer actually credited back and books it in the erp. the credit memo is idempotent
    if outcome not in OUTCOME_STATUS:
        raise ValueError(f"unknown outcome {outcome}")
    if outcome == "lost":
        recovered_cents = 0
    elif outcome == "won":
        recovered_cents = disputed_cents
    if recovered_cents < 0 or recovered_cents > disputed_cents:
        raise ValueError("recovered amount must be between 0 and the disputed amount")
    if outcome == "partial" and not 0 < recovered_cents < disputed_cents:
        raise ValueError("a partial outcome needs an amount strictly between 0 and the disputed amount")
    memo_id = erp.post_credit_memo(retailer, deduction_ref, recovered_cents) if recovered_cents else None
    logger.info(f"{retailer}/{deduction_ref}: outcome={outcome} recovered={recovered_cents} memo={memo_id}")
    return {"dispute_status": OUTCOME_STATUS[outcome], "recovered_cents": recovered_cents, "erp_memo_id": memo_id}
