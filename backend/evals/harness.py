import logging
from collections import deque

from sqlalchemy import select

from db import repo
from db.models import Case
from db.session import init_db, session_scope
from jobs.runner import Runtime, build_runtime
from synth.generate import GeneratorConfig, generate

logger = logging.getLogger(__name__)


def run_batch(cfg: GeneratorConfig, reverse: bool = False) -> tuple[Runtime, dict[str, dict]]:
    # generates a labelled portal, then pushes every deduction through the real pipeline in order
    labels = generate(cfg)
    init_db()
    runtime = build_runtime(emit=lambda event: None, today=lambda: cfg.as_of)
    with session_scope() as session:
        ids = [repo.get_or_create_case(session, r, ref)[0].id for r, ref in runtime.deps.portal.list_deductions()]
    pending = deque(reversed(ids) if reverse else ids)
    budget = len(ids) * 5
    while pending and budget > 0:
        budget -= 1
        pending.extend(runtime.runner.process(pending.popleft()))
    runtime.runner.shutdown()
    return runtime, labels


def collect_rows(labels: dict[str, dict]) -> list[dict]:
    rows = []
    with session_scope() as session:
        for case in session.scalars(select(Case)):
            label = labels[f"{case.retailer}/{case.deduction_ref}"]
            decision = case.state_json.get("decision", {})
            rows.append(
                {
                    "key": f"{case.retailer}/{case.deduction_ref}",
                    "scenario": label["scenario"],
                    "expected": label["decision"],
                    "predicted": case.decision,
                    "rule": decision.get("rule"),
                    "expected_invalid_cents": label["invalid_cents"],
                    "predicted_invalid_cents": case.invalid_cents,
                    "claimed_cents": case.claimed_cents,
                    "confidence": case.confidence,
                    "status": case.status,
                }
            )
    return rows
