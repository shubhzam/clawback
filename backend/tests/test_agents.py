from types import SimpleNamespace

from agents.classifier import classify_document
from agents.extractor import parse_document
from core.llm import MockLLM

DEPS = SimpleNamespace(llm=MockLLM())


def test_classifier_waterfall_stages():
    assert classify_document("PROOF OF DELIVERY\nPOD Number: 1", DEPS)[:2] == ("pod", "regex")
    messy_pod = "RECEIVING REPORT - STORE DC\nReceived By: A\nConsignee: X\nSigned: yes"
    assert classify_document(messy_pod, DEPS)[:2] == ("pod", "keywords")
    assert classify_document("lorem ipsum dolor", DEPS)[0] == "unknown"
    assert classify_document("   ", DEPS) == ("unknown", "none", 0.0)


def test_extractor_handles_aliases_and_lines():
    text = "\n".join(
        [
            "ITEM DETAIL - RETAILER ADJUSTMENT",
            "Ref: WMT-100001",
            "Reason: Shortage",
            "Claim Amount: $1,152.00",
            "Deduction Date: 2026-09-14",
            "Inv No: INV-2026-48211",
            "PO No: 4500187342",
            "SKU: NF-ALM-12",
            "Claimed Qty: 24",
        ]
    )
    fields = parse_document("deduction_notice", text)
    assert fields["deduction_ref"] == "WMT-100001"
    assert fields["reason_code"] == "SHORT"
    assert fields["claimed_amount_cents"] == 115200
    assert fields["invoice_number"] == "INV-2026-48211"
    assert fields["claimed_qty"] == 24

    pod = parse_document("pod", "PROOF OF DELIVERY\nSigned: yes\nLINE | NF-ALM-12 | 144 | 0 | clean")
    assert pod["signed"] is True
    assert pod["lines"] == [{"sku": "NF-ALM-12", "received_qty": 144, "damaged_qty": 0, "notes": "clean"}]
