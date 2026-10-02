import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

_TOKEN = re.compile(r"[a-z0-9]+")
_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

DEFAULT_META = {
    "display_name": "Unknown retailer",
    "dispute_window_days": 30,
    "required_docs": {
        "SHORT": ["pod", "bol"],
        "DMG": ["pod"],
        "PRICE": ["invoice", "po"],
        "OTIF": ["pod", "bol"],
        "PROMO": ["remittance"],
    },
}


@dataclass(frozen=True)
class PolicyChunk:
    retailer: str
    section: str
    text: str
    source: str


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class PolicyStore:
    # small in-process bm25 index over retailer policy sections. same interface a vector db would sit behind
    def __init__(self, chunks: list[PolicyChunk], meta: dict[str, dict]):
        self.chunks = chunks
        self._meta = meta
        self._tf = [Counter(_tokens(c.section + " " + c.text)) for c in chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = (sum(self._len) / len(self._len)) if self._len else 0.0
        doc_freq: Counter = Counter()
        for tf in self._tf:
            doc_freq.update(tf.keys())
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}

    @classmethod
    def from_dir(cls, path: Path) -> "PolicyStore":
        chunks: list[PolicyChunk] = []
        meta: dict[str, dict] = {}
        for md_file in sorted(path.glob("*.md")):
            match = _FRONT_MATTER.match(md_file.read_text())
            if not match:
                continue
            front = yaml.safe_load(match.group(1)) or {}
            retailer = front["retailer"]
            meta[retailer] = {**DEFAULT_META, **front}
            for block in re.split(r"^## ", match.group(2), flags=re.MULTILINE)[1:]:
                heading, _, body = block.partition("\n")
                chunks.append(PolicyChunk(retailer, heading.strip(), body.strip(), md_file.name))
        return cls(chunks, meta)

    def meta(self, retailer: str) -> dict:
        return self._meta.get(retailer, DEFAULT_META)

    def known(self, retailer: str) -> bool:
        return retailer in self._meta

    def search(self, query: str, retailer: str | None = None, k: int = 3) -> list[PolicyChunk]:
        q_tokens = _tokens(query)
        scored = []
        for i, chunk in enumerate(self.chunks):
            if retailer and chunk.retailer != retailer:
                continue
            tf, dl = self._tf[i], self._len[i]
            score = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                num = tf[t] * 2.5
                den = tf[t] + 1.5 * (0.25 + 0.75 * dl / (self._avg_len or 1))
                score += self._idf.get(t, 0.0) * num / den
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [self.chunks[i] for _, i in scored[:k]]
