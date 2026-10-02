from types import SimpleNamespace

import pytest

from agents.classifier import classify_document
from agents.extractor import parse_document
from agents.orchestrator import decide
from agents.validator import check_amount_reconciles, check_invoice_foots
from config import Settings
from core.llm import MockLLM

DEPS = SimpleNamespace(llm=MockLLM())
ORCH_DEPS = SimpleNamespace(settings=Settings())


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


def _finding(status: str, invalid_cents: int, confidence: float) -> dict:
    return {
        "check": "hand_built",
        "status": status,
        "amount_invalid_cents": invalid_cents,
        "confidence": confidence,
        "detail": "hand-built finding for orchestrator test",
    }


ORCHESTRATOR_CASES = [
    pytest.param({"claimed_amount_cents": 0}, [], {"days_remaining": 10}, "ACCEPT", "claim_supported", id="accept"),
    pytest.param(
        {"claimed_amount_cents": 5000},
        [_finding("fail", 5000, 0.95)],
        {"days_remaining": 10, "deadline": "2026-12-01"},
        "DISPUTE",
        "invalid_deduction",
        id="dispute",
    ),
    pytest.param(
        {"claimed_amount_cents": 5000},
        [_finding("fail", 5000, 0.95)],
        {"days_remaining": -1, "deadline": "2026-01-01"},
        "ESCALATE",
        "window_closed",
        id="escalate-window-closed",
    ),
    pytest.param(
        {"claimed_amount_cents": 5000},
        [_finding("fail", 5000, 0.50)],
        {"days_remaining": 10, "deadline": "2026-12-01"},
        "ESCALATE",
        "low_confidence",
        id="escalate-low-confidence",
    ),
    pytest.param(
        {"claimed_amount_cents": 3_000_000},
        [_finding("fail", 3_000_000, 0.95)],
        {"days_remaining": 10, "deadline": "2026-12-01"},
        "ESCALATE",
        "high_value_signoff",
        id="escalate-high-value-signoff",
    ),
    pytest.param(
        {"claimed_amount_cents": 5000},
        [_finding("inconclusive", 0, 0.0)],
        {"days_remaining": 10, "deadline": "2026-12-01"},
        "ESCALATE",
        "inconclusive_evidence",
        id="escalate-inconclusive-evidence",
    ),
]


@pytest.mark.parametrize("notice,findings,policy,expected_decision,expected_rule", ORCHESTRATOR_CASES)
def test_orchestrator_decide_rules(notice, findings, policy, expected_decision, expected_rule):
    result = decide(notice, findings, policy, ORCH_DEPS)
    assert result["decision"] == expected_decision
    assert result["rule"] == expected_rule
