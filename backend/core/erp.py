import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class MockERP:
    # append-only credit memo ledger standing in for a netsuite or quickbooks push
    # posting is idempotent per (retailer, deduction_ref) so a retried recovery never double books cash
    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        self._lock = threading.Lock()

    def _entries(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        return [json.loads(line) for line in self.ledger_path.read_text().splitlines() if line.strip()]

    def post_credit_memo(self, retailer: str, deduction_ref: str, amount_cents: int) -> str:
        with self._lock:
            entries = self._entries()
            for entry in entries:
                if entry["retailer"] == retailer and entry["deduction_ref"] == deduction_ref:
                    return entry["memo_id"]
            memo_id = f"CM-{len(entries) + 1:06d}"
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a") as fh:
                fh.write(
                    json.dumps(
                        {
                            "memo_id": memo_id,
                            "retailer": retailer,
                            "deduction_ref": deduction_ref,
                            "amount_cents": amount_cents,
                        }
                    )
                    + "\n"
                )
            logger.info(f"posted {memo_id} for {retailer}/{deduction_ref}, {amount_cents} cents")
            return memo_id
