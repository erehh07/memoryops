"""Local fallback memory: keyword + field-match scoring over a JSON file.

Used ONLY when Hindsight is not configured or unreachable. The UI shows a
"Memory: local fallback" badge whenever this store is active.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date
from pathlib import Path

from .base import MemoryRecord, MemoryStatus, MemoryStore, RecallQuery, RecalledMemory

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "and", "or", "for", "from", "is", "was", "on", "in", "by", "with",
         "how", "were", "resolved", "vendor", "invoice", "versus", "po", "exceptions", "exception"}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1}


class LocalMemoryStore(MemoryStore):
    name = "local"

    def __init__(self, path: str, reason: str = "local backend selected"):
        self.path = Path(path)
        self.reason = reason
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._records = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._records = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def retain(self, record: MemoryRecord) -> None:
        with self._lock:
            # Same id => replace (vendor facts / approver prefs are rebuilt as they repeat).
            self._records[record.id] = record.model_dump()
            self._save()

    def recall(self, query: RecallQuery, k: int = 5) -> list[RecalledMemory]:
        qtok = _tokens(query.to_text())
        today = date.today()
        scored: list[tuple[float, dict]] = []
        with self._lock:
            records = list(self._records.values())
        for r in records:
            score = 0.0
            if r["vendor_code"] == query.vendor_code:
                score += 3.0
            if r["exception_type"] == query.exception_type:
                score += 3.0
            f = r.get("fields", {})
            if query.variance_pct is not None and f.get("variance_pct"):
                try:
                    d = abs(float(f["variance_pct"]) - query.variance_pct)
                    score += 2.0 * max(0.0, 1.0 - d / 5.0)
                except ValueError:
                    pass
            if query.quantity_delta is not None and f.get("quantity_delta"):
                try:
                    d = abs(float(f["quantity_delta"]) - query.quantity_delta)
                    score += 1.0 * max(0.0, 1.0 - d / 10.0)
                except ValueError:
                    pass
            overlap = len(qtok & _tokens(r["text"]))
            score += 0.1 * overlap
            if r["kind"] != "decision":
                score += 0.5  # distilled facts are compact and useful
            try:
                age = (today - date.fromisoformat(r["date"][:10])).days
                score += 0.3 * max(0.0, 1.0 - age / 365.0)
            except ValueError:
                pass
            # Require at least one structured match; pure keyword overlap is not a precedent.
            if r["vendor_code"] == query.vendor_code or r["exception_type"] == query.exception_type:
                scored.append((score, r))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        out = []
        for score, r in scored[:k]:
            out.append(
                RecalledMemory(
                    id=r["id"], kind=r["kind"], text=r["text"], score=round(score, 3),
                    vendor_code=r["vendor_code"], exception_type=r["exception_type"],
                    fields={**r.get("fields", {}), "date": r["date"]}, source="local",
                )
            )
        return out

    def status(self) -> MemoryStatus:
        return MemoryStatus(backend="local", detail=self.reason, record_count=len(self._records))

    def reset(self) -> None:
        with self._lock:
            self._records = {}
            self._save()
