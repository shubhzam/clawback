import json
import time
from datetime import date

from fastapi.testclient import TestClient

from config import get_settings
from synth.generate import GeneratorConfig, generate

DONE = {"ACCEPTED", "DISPUTE_DRAFTED", "ESCALATED", "ERROR"}


def _wait_for_processing(client: TestClient, expected: int, timeout: float = 45.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cases = client.get("/cases").json()
        if len(cases) == expected and all(c["status"] in DONE for c in cases):
            return cases
        time.sleep(0.2)
    raise AssertionError("cases did not finish processing in time")


def test_full_lifecycle_through_the_api():
    generate(GeneratorConfig(count=40, seed=3, as_of=date.today(), write_pdf=False, out_dir=get_settings().resolved_portal_root()))
    from main import app

    with TestClient(app) as client:
        first = client.post("/portals/sync")
        assert first.status_code == 202 and first.json()["created"] == 40
        assert client.post("/portals/sync").json()["created"] == 0

        cases = _wait_for_processing(client, 40)
        assert not [c for c in cases if c["status"] == "ERROR"]

        stats = client.get("/stats").json()
        assert stats["total_cases"] == 40 and stats["flagged_invalid_cents"] > 0

        disputed = next(c for c in cases if c["status"] == "DISPUTE_DRAFTED")
        detail = client.get(f"/cases/{disputed['id']}").json()
        assert detail["dispute"]["status"] == "DRAFTED" and detail["audit"]

        filed = client.post(f"/cases/{disputed['id']}/dispute/file").json()
        assert filed["status"] == "DISPUTE_FILED" and filed["dispute"]["confirmation_id"].startswith("DSP-")

        amount = filed["dispute"]["amount_cents"]
        bad = client.post(f"/cases/{disputed['id']}/dispute/outcome", json={"outcome": "partial", "recovered_cents": amount})
        assert bad.status_code == 422
        won = client.post(f"/cases/{disputed['id']}/dispute/outcome", json={"outcome": "won"}).json()
        assert won["status"] == "RECOVERED" and won["recovered_cents"] == amount
        again = client.post(f"/cases/{disputed['id']}/dispute/outcome", json={"outcome": "won"}).json()
        assert again["dispute"]["erp_memo_id"] == won["dispute"]["erp_memo_id"]
        ledger = get_settings().resolved_erp_ledger().read_text().splitlines()
        assert len([ln for ln in ledger if json.loads(ln)["deduction_ref"] == disputed["deduction_ref"]]) == 1

        assert client.get("/stats").json()["dispute_win_rate"] == 1.0

        escalated = next(c for c in cases if c["status"] == "ESCALATED")
        accepted = client.post(
            f"/cases/{escalated['id']}/review", json={"action": "accept_deduction", "reviewer": "ap-analyst", "note": "agreed"}
        )
        assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED"
        assert client.post(f"/cases/{escalated['id']}/review", json={"action": "accept_deduction", "reviewer": "x"}).status_code == 409

        assert client.get("/cases/999999").status_code == 404


def test_upload_rejects_unsafe_names():
    from main import app

    with TestClient(app) as client:
        resp = client.post(
            "/cases",
            data={"retailer": "walmart", "deduction_ref": "WMT-1"},
            files=[("files", ("bad name.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 422
        resp = client.post(
            "/cases",
            data={"retailer": "../etc", "deduction_ref": "WMT-1"},
            files=[("files", ("ok.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 422
