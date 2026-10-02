from datetime import date

from sqlalchemy import func, select

from db.models import AuditEvent, Case, Dispute
from db.session import session_scope
from evals.harness import collect_rows, run_batch
from synth.generate import GeneratorConfig

AS_OF = date(2026, 10, 2)


def _cfg(count: int, **kwargs) -> GeneratorConfig:
    return GeneratorConfig(count=count, seed=11, as_of=AS_OF, write_pdf=False, **kwargs)


def test_clean_documents_match_every_label():
    _, labels = run_batch(_cfg(120))
    rows = collect_rows(labels)
    wrong = [r for r in rows if r["expected"] != r["predicted"]]
    assert not wrong, wrong[:3]
    disputes = [r for r in rows if r["expected"] == "DISPUTE"]
    assert all(r["expected_invalid_cents"] == r["predicted_invalid_cents"] for r in disputes)


def test_duplicate_detection_is_order_independent():
    _, labels = run_batch(_cfg(120), reverse=True)
    wrong = [r for r in collect_rows(labels) if r["expected"] != r["predicted"]]
    assert not wrong, wrong[:3]


def test_pdf_documents_survive_the_ocr_path():
    cfg = GeneratorConfig(count=12, seed=5, as_of=AS_OF, write_pdf=True)
    _, labels = run_batch(cfg)
    wrong = [r for r in collect_rows(labels) if r["expected"] != r["predicted"]]
    assert not wrong, wrong[:3]


def test_noisy_documents_never_dispute_more_than_claimed():
    _, labels = run_batch(_cfg(150, noise_rate=0.25))
    for row in collect_rows(labels):
        assert 0 <= row["predicted_invalid_cents"] <= row["claimed_cents"]


def test_reprocessing_is_idempotent():
    runtime, labels = run_batch(_cfg(40))
    with session_scope() as session:
        case = session.scalars(select(Case).where(Case.decision == "DISPUTE")).first()
        case_id, before_status = case.id, case.status
        docs_before = len(case.documents)
        audit_before = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.case_id == case_id))

    runtime.runner.process(case_id)

    with session_scope() as session:
        case = session.get(Case, case_id)
        assert case.status == before_status
        assert len(case.documents) == docs_before
        assert session.scalar(select(func.count()).select_from(Dispute).where(Dispute.case_id == case_id)) == 1
        audit_after = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.case_id == case_id))
        assert audit_after > audit_before


def test_every_dispute_letter_cites_policy_and_names_the_amount():
    _, _ = run_batch(_cfg(60))
    with session_scope() as session:
        disputes = session.scalars(select(Dispute)).all()
        assert disputes
        for dispute in disputes:
            assert dispute.citations, dispute.case_id
            assert "Dispute of deduction" in dispute.letter
            assert dispute.amount_cents > 0
