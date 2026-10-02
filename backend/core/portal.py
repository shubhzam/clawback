import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DOC_SUFFIXES = {".pdf", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class PortalDocument:
    filename: str
    path: Path


class PortalConnector(Protocol):
    def list_deductions(self, retailer: str | None = None) -> list[tuple[str, str]]: ...

    def fetch_documents(self, retailer: str, deduction_ref: str) -> list[PortalDocument]: ...

    def submit_dispute(self, retailer: str, deduction_ref: str, letter: str, attachments: list[str]) -> str: ...


class FilesystemPortal:
    # stand-in for real retailer portal agents. layout is root/<retailer>/<deduction_ref>/<files>
    # a production connector would log in with stored creds and scrape or call the portal api
    def __init__(self, root: Path):
        self.root = root

    def folder(self, retailer: str, deduction_ref: str) -> Path:
        return self.root / retailer / deduction_ref

    def list_deductions(self, retailer: str | None = None) -> list[tuple[str, str]]:
        if not self.root.exists():
            return []
        retailers = [retailer] if retailer else sorted(p.name for p in self.root.iterdir() if p.is_dir())
        found = []
        for name in retailers:
            if name.startswith("_"):
                continue
            base = self.root / name
            if not base.is_dir():
                continue
            for ref_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                found.append((name, ref_dir.name))
        return found

    def fetch_documents(self, retailer: str, deduction_ref: str) -> list[PortalDocument]:
        base = self.folder(retailer, deduction_ref)
        if not base.is_dir():
            logger.warning(f"no portal folder for {retailer}/{deduction_ref}")
            return []
        files = sorted(p for p in base.iterdir() if p.suffix.lower() in DOC_SUFFIXES)
        return [PortalDocument(filename=p.name, path=p) for p in files]

    def submit_dispute(self, retailer: str, deduction_ref: str, letter: str, attachments: list[str]) -> str:
        # confirmation id is derived from the ref so refiling the same dispute is idempotent
        confirmation = "DSP-" + hashlib.sha1(f"{retailer}:{deduction_ref}".encode()).hexdigest()[:8].upper()
        out_dir = self.root / "_submitted" / retailer
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "confirmation_id": confirmation,
            "deduction_ref": deduction_ref,
            "attachments": attachments,
            "letter": letter,
        }
        (out_dir / f"{deduction_ref}.json").write_text(json.dumps(payload, indent=2))
        logger.info(f"submitted dispute {confirmation} for {retailer}/{deduction_ref}")
        return confirmation
