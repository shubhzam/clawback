from config import get_settings
from core.money import fmt_cents, to_cents
from core.retrieval import PolicyStore


def test_money_round_trip():
    assert to_cents("$1,234.50") == 123450
    assert to_cents("48") == 4800
    assert to_cents("n/a") is None
    assert fmt_cents(123450) == "$1,234.50"
    assert fmt_cents(5) == "$0.05"


def test_policy_search_finds_the_right_clause():
    store = PolicyStore.from_dir(get_settings().policies_dir)
    top = store.search("damage must be noted on proof of delivery", "kroger", k=1)[0]
    assert top.retailer == "kroger" and "Damage" in top.section
    assert store.meta("kroger")["dispute_window_days"] == 21
    assert store.meta("unknown-retailer")["dispute_window_days"] == 30
