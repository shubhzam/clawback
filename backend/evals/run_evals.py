import argparse
import json
import os
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

DECISIONS = ["DISPUTE", "ACCEPT", "ESCALATE"]


def score(rows: list[dict]) -> dict:
    n = len(rows)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        confusion[r["expected"]][r["predicted"]] += 1
    per_class = {}
    for cls in DECISIONS:
        tp = confusion[cls][cls]
        fp = sum(confusion[o][cls] for o in DECISIONS if o != cls)
        fn = sum(confusion[cls][p] for p in DECISIONS if p != cls)
        per_class[cls] = {
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "support": sum(confusion[cls].values()),
        }

    should_dispute = [r for r in rows if r["expected"] == "DISPUTE"]
    both = [r for r in should_dispute if r["predicted"] == "DISPUTE"]
    amount_errors = [abs(r["predicted_invalid_cents"] - r["expected_invalid_cents"]) for r in both]
    wrongly_disputed = [r for r in rows if r["predicted"] == "DISPUTE" and r["expected"] != "DISPUTE"]
    target = sum(r["expected_invalid_cents"] for r in should_dispute)
    captured = sum(min(r["predicted_invalid_cents"], r["expected_invalid_cents"]) for r in both)

    by_scenario: dict[str, dict] = defaultdict(lambda: {"n": 0, "correct": 0})
    for r in rows:
        by_scenario[r["scenario"]]["n"] += 1
        by_scenario[r["scenario"]]["correct"] += r["expected"] == r["predicted"]

    return {
        "cases": n,
        "decision_accuracy": sum(r["expected"] == r["predicted"] for r in rows) / n,
        "per_class": per_class,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "invalid_amount_exact_rate": (sum(e == 0 for e in amount_errors) / len(amount_errors)) if amount_errors else None,
        "invalid_amount_mae_cents": (sum(amount_errors) / len(amount_errors)) if amount_errors else None,
        "dollars_recoverable_cents": target,
        "dollars_captured_cents": captured,
        "dollar_recall": captured / target if target else None,
        "dollars_wrongly_disputed_cents": sum(r["predicted_invalid_cents"] for r in wrongly_disputed),
        "by_scenario": dict(by_scenario),
    }


def print_report(result: dict) -> None:
    print(f"cases: {result['cases']}  decision accuracy: {result['decision_accuracy']:.3f}")
    print("\nper class")
    for cls, m in result["per_class"].items():
        p = "n/a" if m["precision"] is None else f"{m['precision']:.3f}"
        r = "n/a" if m["recall"] is None else f"{m['recall']:.3f}"
        print(f"  {cls:<9} precision {p}  recall {r}  support {m['support']}")
    print("\nconfusion (rows expected, cols predicted)")
    for exp in DECISIONS:
        row = result["confusion"].get(exp, {})
        print(f"  {exp:<9}" + "".join(f"{p}={row.get(p, 0):<5}" for p in DECISIONS))
    print(f"\ninvalid amount exact on disputed cases: {result['invalid_amount_exact_rate']}")
    print(f"dollars recoverable ${result['dollars_recoverable_cents'] / 100:,.2f}, captured ${result['dollars_captured_cents'] / 100:,.2f}")
    print(f"dollars wrongly disputed ${result['dollars_wrongly_disputed_cents'] / 100:,.2f}")
    print("\nby scenario")
    for name, m in sorted(result["by_scenario"].items()):
        print(f"  {name:<22} {m['correct']}/{m['n']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="run the pipeline on synthetic labelled deductions and score it")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 2))
    parser.add_argument("--noise", type=float, default=0.10, help="share of documents with ocr style damage")
    parser.add_argument("--txt", action="store_true", help="use plain text documents instead of pdf")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "results.json")
    args = parser.parse_args()

    # point the app at a throwaway storage dir before any app module reads settings
    workdir = tempfile.mkdtemp(prefix="clawback-eval-")
    os.environ["CLAWBACK_STORAGE_DIR"] = workdir
    os.environ["CLAWBACK_LLM_PROVIDER"] = "mock"

    from evals.harness import collect_rows, run_batch
    from synth.generate import GeneratorConfig

    start = time.perf_counter()
    _, labels = run_batch(GeneratorConfig(count=args.count, seed=args.seed, as_of=args.as_of, noise_rate=args.noise, write_pdf=not args.txt))
    elapsed = time.perf_counter() - start
    rows = collect_rows(labels)
    result = score(rows)
    result["seconds_per_case"] = elapsed / len(rows)
    result["config"] = {"count": args.count, "seed": args.seed, "as_of": args.as_of.isoformat(), "noise": args.noise, "pdf": not args.txt}
    result["misses"] = [r for r in rows if r["expected"] != r["predicted"]]
    args.out.write_text(json.dumps(result, indent=2))
    print_report(result)
    print(f"\n{result['seconds_per_case'] * 1000:.0f} ms per case, results written to {args.out}")


if __name__ == "__main__":
    main()
