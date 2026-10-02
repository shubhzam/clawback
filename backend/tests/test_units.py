from agents.validator import check_amount_reconciles, check_invoice_foots


def _invoice(qty: int, stated: int) -> dict:
    return {"invoice": {"fields": {"lines": [{"sku": "A", "qty": qty, "unit_price_cents": 4800}], "amount_due_cents": stated}}}


def test_invoice_footing_flags_damaged_invoice():
    assert check_invoice_foots(_invoice(10, 48000)) is None
    flagged = check_invoice_foots(_invoice(10, 48500))
    assert flagged and flagged["status"] == "inconclusive"


def test_amount_reconciliation():
    notice = {"reason_code": "SHORT", "sku": "A", "claimed_qty": 5, "claimed_amount_cents": 24000}
    assert check_amount_reconciles(notice, _invoice(10, 48000)) is None
    notice["claimed_amount_cents"] = 23000
    assert check_amount_reconciles(notice, _invoice(10, 48000))["status"] == "inconclusive"
