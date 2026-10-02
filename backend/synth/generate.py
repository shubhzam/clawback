import argparse
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from config import get_settings
from core.retrieval import PolicyStore
from synth import documents as docs
from synth.documents import Order

logger = logging.getLogger(__name__)

CATALOG = [
    ("NF-ALM-12", "Almond Butter 12oz Case", 4800),
    ("NF-GRN-06", "Granola Bites 6ct Case", 3150),
    ("NF-SPK-24", "Sparkling Water 24pk", 2640),
    ("NF-OAT-10", "Oat Bar Variety 10ct Case", 3925),
    ("NF-HYD-12", "Electrolyte Mix 12ct Case", 5480),
    ("NF-CHP-18", "Lip Balm 18ct Display", 2275),
]
PREFIXES = {"walmart": "WMT", "kehe": "KHE", "unfi": "UNF", "kroger": "KRG"}
REASON_TEXT = {
    "SHORT": "Shortage - units not received",
    "DMG": "Damaged merchandise at receipt",
    "PRICE": "Price variance against purchase order",
    "OTIF": "Late delivery compliance fine",
    "PROMO": "Promotional allowance",
}
SCENARIO_WEIGHTS = {
    "shortage_invalid": 0.13, "shortage_partial": 0.06, "shortage_valid": 0.08,
    "damage_invalid": 0.10, "damage_valid": 0.07,
    "pricing_invalid": 0.09, "pricing_valid": 0.04,
    "otif_invalid": 0.08, "otif_valid": 0.05,
    "duplicate": 0.06, "missing_pod": 0.04, "window_expired": 0.04,
    "promo_unverifiable": 0.05, "high_value_shortage": 0.03,
    "unsigned_pod": 0.03, "mismatched_docs": 0.03,
}


@dataclass
class GeneratorConfig:
    count: int = 120
    seed: int = 7
    as_of: date = field(default_factory=date.today)
    messy_rate: float = 0.15
    remittance_rate: float = 0.35
    noise_rate: float = 0.0
    write_pdf: bool = True
    out_dir: Path = field(default_factory=lambda: get_settings().resolved_portal_root())


@dataclass
class Built:
    order: Order
    skip_docs: set[str]
    label: dict
    pod_po_override: str | None = None


def _prorate(claimed: int, invalid_qty: int, claimed_qty: int) -> int:
    return (claimed * invalid_qty + claimed_qty // 2) // claimed_qty


class Factory:
    def __init__(self, cfg: GeneratorConfig, windows: dict[str, int]):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.windows = windows
        self.used_refs: set[str] = set()

    def _unique(self, prefix: str) -> str:
        while True:
            ref = f"{prefix}-{self.rng.randint(100000, 999999)}"
            if ref not in self.used_refs:
                self.used_refs.add(ref)
                return ref

    def base_order(self, retailer: str, reason: str, days_ago: int | None = None, shipped: int | None = None) -> Order:
        rng = self.rng
        sku, desc, price = rng.choice(CATALOG)
        window = self.windows[retailer]
        age = days_ago if days_ago is not None else rng.randint(3, max(4, window - 4))
        deduction_date = self.cfg.as_of - timedelta(days=age)
        delivered_day = deduction_date - timedelta(days=rng.randint(8, 18))
        appt_start = datetime.combine(delivered_day, time(8, 0)) + timedelta(hours=rng.choice([0, 2, 4]))
        appt_end = appt_start + timedelta(hours=4)
        others = rng.sample([c for c in CATALOG if c[0] != sku], rng.randint(0, 2))
        order = Order(
            retailer=retailer,
            display_name=retailer.upper() if retailer == "unfi" else retailer.capitalize(),
            ref=self._unique(PREFIXES[retailer]),
            reason=reason,
            reason_text=REASON_TEXT[reason],
            sku=sku, desc=desc, price=price,
            shipped=shipped or rng.choice([48, 72, 96, 120, 144, 192, 240]),
            received=0,
            po_number=str(rng.randint(4_500_000_000, 4_599_999_999)),
            invoice_number=f"INV-{deduction_date.year}-{rng.randint(10000, 99999)}",
            pod_number=f"POD-{rng.randint(100000, 999999)}",
            bol_number=str(rng.randint(10**16, 10**17 - 1)),
            ship_date=delivered_day - timedelta(days=rng.randint(1, 3)),
            deduction_date=deduction_date,
            delivered_at=appt_start + timedelta(minutes=rng.randint(10, 200)),
            appt_start=appt_start,
            appt_end=appt_end,
            claimed_cents=0,
            other_lines=[(s, d, rng.choice([24, 48, 72]), p) for s, d, p in others],
        )
        order.received = order.shipped
        return order

    def shortage(self, retailer: str, kind: str) -> Built:
        o = self.base_order(retailer, "SHORT")
        o.claimed_qty = self.rng.randint(6, 36)
        o.claimed_cents = o.claimed_qty * o.price
        if kind == "valid":
            o.received = o.shipped - o.claimed_qty
            return Built(o, set(), {"decision": "ACCEPT", "invalid_cents": 0})
        if kind == "partial":
            actual = self.rng.randint(1, o.claimed_qty // 2)
            o.received = o.shipped - actual
            invalid = _prorate(o.claimed_cents, o.claimed_qty - actual, o.claimed_qty)
            return Built(o, set(), {"decision": "DISPUTE", "invalid_cents": invalid})
        return Built(o, set(), {"decision": "DISPUTE", "invalid_cents": o.claimed_cents})

    def damage(self, retailer: str, valid: bool) -> Built:
        o = self.base_order(retailer, "DMG")
        o.claimed_qty = self.rng.randint(4, 24)
        o.claimed_cents = o.claimed_qty * o.price
        if valid:
            o.damaged = o.claimed_qty
            o.pod_notes = "Crushed cartons noted at dock"
            return Built(o, set(), {"decision": "ACCEPT", "invalid_cents": 0})
        return Built(o, set(), {"decision": "DISPUTE", "invalid_cents": o.claimed_cents})

    def pricing(self, retailer: str, valid: bool) -> Built:
        o = self.base_order(retailer, "PRICE")
        delta = max(1, round(o.price * self.rng.choice([0.05, 0.08, 0.10, 0.15])))
        o.claimed_qty = o.shipped
        o.claimed_po_price = o.price - delta
        o.claimed_cents = delta * o.shipped
        if valid:
            o.po_price = o.price - delta
            return Built(o, set(), {"decision": "ACCEPT", "invalid_cents": 0})
        return Built(o, set(), {"decision": "DISPUTE", "invalid_cents": o.claimed_cents})

    def otif(self, retailer: str, in_window: bool) -> Built:
        o = self.base_order(retailer, "OTIF")
        total = o.shipped * o.price + sum(q * p for _, _, q, p in o.other_lines)
        o.claimed_cents = round(total * 0.03)
        if in_window:
            return Built(o, set(), {"decision": "DISPUTE", "invalid_cents": o.claimed_cents})
        o.delivered_at = o.appt_end + timedelta(hours=self.rng.randint(3, 20))
        return Built(o, set(), {"decision": "ACCEPT", "invalid_cents": 0})

    def duplicate(self, retailer: str) -> list[Built]:
        first = self.shortage(retailer, "valid")
        twin = Order(**{**first.order.__dict__, "ref": self._unique(PREFIXES[retailer])})
        twin.deduction_date = first.order.deduction_date + timedelta(days=self.rng.randint(1, 3))
        twin_built = Built(twin, set(), {"decision": "DISPUTE", "invalid_cents": twin.claimed_cents})
        return [first, twin_built]

    def missing_pod(self, retailer: str) -> Built:
        built = self.shortage(retailer, "invalid")
        return Built(built.order, {"pod"}, {"decision": "ESCALATE", "invalid_cents": 0})

    def window_expired(self, retailer: str) -> Built:
        o = self.base_order(retailer, "SHORT", days_ago=self.windows[retailer] + self.rng.randint(5, 25))
        o.claimed_qty = self.rng.randint(6, 36)
        o.claimed_cents = o.claimed_qty * o.price
        return Built(o, set(), {"decision": "ESCALATE", "invalid_cents": o.claimed_cents})

    def promo(self, retailer: str) -> Built:
        o = self.base_order(retailer, "PROMO")
        o.claimed_cents = self.rng.randint(150, 900) * 100
        return Built(o, {"invoice", "pod", "bol", "po"}, {"decision": "ESCALATE", "invalid_cents": 0})

    def high_value(self, retailer: str) -> Built:
        o = self.base_order(retailer, "SHORT", shipped=600)
        o.sku, o.desc, o.price = CATALOG[4]
        o.claimed_qty = 520
        o.claimed_cents = o.claimed_qty * o.price
        return Built(o, set(), {"decision": "ESCALATE", "invalid_cents": o.claimed_cents})

    def unsigned_pod(self, retailer: str) -> Built:
        built = self.shortage(retailer, "invalid")
        built.order.signed = False
        return Built(built.order, set(), {"decision": "ESCALATE", "invalid_cents": built.order.claimed_cents})

    def mismatched(self, retailer: str) -> Built:
        built = self.shortage(retailer, "invalid")
        return Built(built.order, set(), {"decision": "ESCALATE", "invalid_cents": 0}, pod_po_override="4599000000")

    def build(self, scenario: str, retailer: str) -> list[Built]:
        simple = {
            "shortage_invalid": lambda: self.shortage(retailer, "invalid"),
            "shortage_partial": lambda: self.shortage(retailer, "partial"),
            "shortage_valid": lambda: self.shortage(retailer, "valid"),
            "damage_invalid": lambda: self.damage(retailer, False),
            "damage_valid": lambda: self.damage(retailer, True),
            "pricing_invalid": lambda: self.pricing(retailer, False),
            "pricing_valid": lambda: self.pricing(retailer, True),
            "otif_invalid": lambda: self.otif(retailer, True),
            "otif_valid": lambda: self.otif(retailer, False),
            "missing_pod": lambda: self.missing_pod(retailer),
            "window_expired": lambda: self.window_expired(retailer),
            "promo_unverifiable": lambda: self.promo(retailer),
            "high_value_shortage": lambda: self.high_value(retailer),
            "unsigned_pod": lambda: self.unsigned_pod(retailer),
            "mismatched_docs": lambda: self.mismatched(retailer),
        }
        if scenario == "duplicate":
            return self.duplicate(retailer)
        return [simple[scenario]()]


def corrupt(text: str, rng: random.Random) -> str:
    # mimics ocr damage: a dropped line, a dropped digit or a swapped digit somewhere past the title
    lines = text.splitlines()
    body = [i for i in range(1, len(lines))]
    kind = rng.choice(["drop_line", "digit_drop", "digit_swap"])
    if kind == "drop_line":
        del lines[rng.choice(body)]
        return "\n".join(lines)
    numeric = [i for i in body if any(c.isdigit() for c in lines[i])]
    i = rng.choice(numeric)
    chars = list(lines[i])
    j = rng.choice([k for k, c in enumerate(chars) if c.isdigit()])
    if kind == "digit_drop":
        del chars[j]
    else:
        chars[j] = str((int(chars[j]) + rng.randint(1, 9)) % 10)
    lines[i] = "".join(chars)
    return "\n".join(lines)


def _write_doc(path: Path, text: str, as_pdf: bool) -> Path:
    if not as_pdf:
        target = path.with_suffix(".txt")
        target.write_text(text)
        return target
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    target = path.with_suffix(".pdf")
    pdf = canvas.Canvas(str(target), pagesize=letter)
    pdf.setFont("Courier", 9)
    y = 750
    for line in text.splitlines():
        pdf.drawString(48, y, line)
        y -= 13
    pdf.save()
    return target


def _write_case(built: Built, cfg: GeneratorConfig, rng: random.Random) -> list[str]:
    o = built.order
    folder = cfg.out_dir / o.retailer / o.ref
    folder.mkdir(parents=True, exist_ok=True)
    renderers = {
        "deduction_notice": docs.render_notice,
        "invoice": docs.render_invoice,
        "pod": lambda order, m: docs.render_pod(order, m, built.pod_po_override),
        "bol": docs.render_bol,
        "po": docs.render_po,
    }
    types = [t for t in renderers if t not in built.skip_docs]
    if o.reason != "PROMO" and rng.random() < cfg.remittance_rate:
        renderers["remittance"] = docs.render_remittance
        types.append("remittance")
    if o.reason == "PROMO":
        renderers["remittance"] = docs.render_remittance
        types.append("remittance")
    names = []
    for doc_type in types:
        messy = rng.random() < cfg.messy_rate
        # opaque file names on purpose, classification must come from content
        name = f"doc_{rng.randint(1000, 9999)}" if messy else doc_type
        text = renderers[doc_type](o, messy)
        if cfg.noise_rate and rng.random() < cfg.noise_rate:
            text = corrupt(text, rng)
        written = _write_doc(folder / name, text, cfg.write_pdf)
        names.append(written.name)
    return names


def generate(cfg: GeneratorConfig) -> dict:
    policies = PolicyStore.from_dir(get_settings().policies_dir)
    windows = {r: int(policies.meta(r)["dispute_window_days"]) for r in PREFIXES}
    factory = Factory(cfg, windows)
    rng = factory.rng
    scenarios = list(SCENARIO_WEIGHTS)
    weights = list(SCENARIO_WEIGHTS.values())
    labels: dict[str, dict] = {}
    cfg.out_dir.mkdir(parents=True, exist_ok=True)

    produced = 0
    while produced < cfg.count:
        scenario = rng.choices(scenarios, weights)[0]
        retailer = rng.choice(list(PREFIXES))
        for built in factory.build(scenario, retailer):
            _write_case(built, cfg, rng)
            o = built.order
            labels[f"{o.retailer}/{o.ref}"] = {**built.label, "scenario": scenario, "claimed_cents": o.claimed_cents}
            produced += 1

    (cfg.out_dir / "_labels.json").write_text(json.dumps(labels, indent=2))
    (cfg.out_dir / "_meta.json").write_text(json.dumps({"as_of": cfg.as_of.isoformat(), "seed": cfg.seed, "count": len(labels)}))
    logger.info(f"wrote {len(labels)} deductions to {cfg.out_dir}")
    return labels


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="generate synthetic retailer portal data with ground truth labels")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--messy-rate", type=float, default=0.15)
    parser.add_argument("--noise-rate", type=float, default=0.0, help="share of documents with ocr style damage")
    parser.add_argument("--txt", action="store_true", help="write plain text instead of pdf")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    cfg = GeneratorConfig(count=args.count, seed=args.seed, as_of=args.as_of, messy_rate=args.messy_rate, noise_rate=args.noise_rate, write_pdf=not args.txt)
    if args.out:
        cfg.out_dir = args.out
    labels = generate(cfg)
    by_decision: dict[str, int] = {}
    for label in labels.values():
        by_decision[label["decision"]] = by_decision.get(label["decision"], 0) + 1
    print(f"generated {len(labels)} deductions: {by_decision}")


if __name__ == "__main__":
    main()
