from dataclasses import dataclass, field
from datetime import date, datetime

from core.money import fmt_cents

VENDOR = "Northfield Foods (synthetic vendor)"


@dataclass
class Order:
    # one shipment and the deduction a retailer took against it
    retailer: str
    display_name: str
    ref: str
    reason: str
    reason_text: str
    sku: str
    desc: str
    price: int
    shipped: int
    received: int
    po_number: str
    invoice_number: str
    pod_number: str
    bol_number: str
    ship_date: date
    deduction_date: date
    delivered_at: datetime
    appt_start: datetime
    appt_end: datetime
    claimed_cents: int
    claimed_qty: int | None = None
    claimed_po_price: int | None = None
    po_price: int | None = None
    damaged: int = 0
    signed: bool = True
    pod_notes: str = "No exceptions noted"
    other_lines: list[tuple[str, str, int, int]] = field(default_factory=list)

    @property
    def effective_po_price(self) -> int:
        return self.po_price if self.po_price is not None else self.price


REASON_WORD = {"SHORT": "Shortage", "DMG": "Damage", "PRICE": "Pricing", "OTIF": "Late Delivery", "PROMO": "Promo"}


def _title(clean: str, messy: str, is_messy: bool) -> str:
    return messy if is_messy else clean


def render_notice(o: Order, messy: bool) -> str:
    lines = [
        _title("DEDUCTION NOTICE", "ITEM DETAIL - RETAILER ADJUSTMENT", messy),
        f"Retailer: {o.display_name}",
        f"{'Ref' if messy else 'Deduction Ref'}: {o.ref}",
        f"{'Reason' if messy else 'Reason Code'}: {REASON_WORD[o.reason] if messy else o.reason}",
        f"Reason Description: {o.reason_text}",
        f"{'Claim Amount' if messy else 'Claimed Amount'}: {fmt_cents(o.claimed_cents)}",
        f"Deduction Date: {o.deduction_date.isoformat()}",
        f"{'Inv No' if messy else 'Invoice Number'}: {o.invoice_number}",
        f"{'PO No' if messy else 'PO Number'}: {o.po_number}",
    ]
    if o.reason in {"SHORT", "DMG", "PRICE"}:
        lines.append(f"SKU: {o.sku}")
        lines.append(f"Claimed Qty: {o.claimed_qty}")
    if o.claimed_po_price is not None:
        lines.append(f"Claimed PO Unit Price: {fmt_cents(o.claimed_po_price)}")
    lines.append(f"Vendor: {VENDOR}")
    if messy:
        lines.append("Type: Chargeback")
    return "\n".join(lines)


def _all_lines(o: Order, qty_for_main: int, price_for_main: int) -> list[tuple[str, str, int, int]]:
    return [(o.sku, o.desc, qty_for_main, price_for_main), *o.other_lines]


def render_invoice(o: Order, messy: bool) -> str:
    rows = _all_lines(o, o.shipped, o.price)
    total = sum(qty * price for _, _, qty, price in rows)
    lines = [
        _title("INVOICE", "TAX INVOICE / PAGE 1 OF 1", messy),
        f"{'Inv No' if messy else 'Invoice Number'}: {o.invoice_number}",
        f"PO Number: {o.po_number}",
        f"Ship Date: {o.ship_date.isoformat()}",
        "Terms: Net 30",
        f"Bill To: {o.display_name} Accounts Payable",
        f"Vendor: {VENDOR}",
        "SKU | Description | Qty | Unit Price",
    ]
    lines += [f"LINE | {sku} | {desc} | {qty} | {fmt_cents(price)}" for sku, desc, qty, price in rows]
    lines.append(f"Amount Due: {fmt_cents(total)}")
    return "\n".join(lines)


def render_pod(o: Order, messy: bool, po_number: str | None = None) -> str:
    lines = [
        _title("PROOF OF DELIVERY", "RECEIVING REPORT - STORE DC", messy),
        f"POD Number: {o.pod_number}",
        f"PO Number: {po_number or o.po_number}",
        f"{'Received On' if messy else 'Delivered At'}: {o.delivered_at.isoformat()}",
        "Received By: J. Alvarez, receiving dock",
        f"Consignee: {o.display_name} DC",
        f"Signed: {'yes' if o.signed else 'no'}",
        "SKU | Received Qty | Damaged Qty | Notes",
        f"LINE | {o.sku} | {o.received} | {o.damaged} | {o.pod_notes}",
    ]
    lines += [f"LINE | {sku} | {qty} | 0 | No exceptions noted" for sku, _, qty, _ in o.other_lines]
    return "\n".join(lines)


def render_bol(o: Order, messy: bool) -> str:
    lines = [
        _title("BILL OF LADING", "SHIPMENT DOC 0098-A", messy),
        f"BOL Number: {o.bol_number}",
        f"PO Number: {o.po_number}",
        f"Ship Date: {o.ship_date.isoformat()}",
        "Carrier: Lakeshore Freight Lines",
        "SCAC: LKSF",
        f"{'Window Start' if messy else 'Appointment Start'}: {o.appt_start.isoformat()}",
        f"{'Window End' if messy else 'Appointment End'}: {o.appt_end.isoformat()}",
        "SKU | Shipped Qty | Pallets",
        f"LINE | {o.sku} | {o.shipped} | {max(1, o.shipped // 48)}",
    ]
    lines += [f"LINE | {sku} | {qty} | {max(1, qty // 48)}" for sku, _, qty, _ in o.other_lines]
    return "\n".join(lines)


def render_po(o: Order, messy: bool) -> str:
    lines = [
        _title("PURCHASE ORDER", "ORDER CONFIRMATION", messy),
        f"{'PO No' if messy else 'PO Number'}: {o.po_number}",
        f"Buyer: {o.display_name} category buyer",
        f"Ship Window: {o.ship_date.isoformat()} to {o.ship_date.isoformat()}",
        "SKU | Ordered Qty | Unit Price",
        f"LINE | {o.sku} | {o.shipped} | {fmt_cents(o.effective_po_price)}",
    ]
    lines += [f"LINE | {sku} | {qty} | {fmt_cents(price)}" for sku, _, qty, price in o.other_lines]
    return "\n".join(lines)


def render_remittance(o: Order, messy: bool) -> str:
    return "\n".join(
        [
            _title("REMITTANCE ADVICE", "PAYMENT DETAIL", messy),
            f"Remittance Number: RA-{o.invoice_number[-6:]}",
            "Check Number: 70418233",
            f"Payment Date: {o.deduction_date.isoformat()}",
            f"Deduction Taken: {o.ref} {fmt_cents(o.claimed_cents)}",
            f"Net Paid Amount: {fmt_cents(o.shipped * o.price - o.claimed_cents)}",
        ]
    )
