"""MemoryStore interface. The rest of the app talks to memory ONLY through this module.

Record kinds:
  decision        one resolved exception (the precedent). id = "PREC-<decision id>"
  vendor_fact     learned vendor-level pattern, rebuilt when decisions repeat. id = "VF-<vendor>-<type>"
  approver_pref   an approver's repeated behaviour for a vendor/type. id = "AP-<approver>-<vendor>-<type>"
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Literal, Optional

from pydantic import BaseModel, Field

RecordKind = Literal["decision", "vendor_fact", "approver_pref"]

# Header line embedded in every retained text so a record can be re-identified from raw text.
HEADER_RE = re.compile(r"\[MEMORYOPS (?P<id>[A-Za-z0-9_\-\.]+)\]")


class MemoryRecord(BaseModel):
    id: str
    kind: RecordKind
    vendor_code: str
    vendor_name: str
    exception_type: str
    text: str
    date: str  # ISO date of the underlying event
    fields: dict[str, str] = Field(default_factory=dict)  # flat string metadata

    def tags(self) -> list[str]:
        return [f"vendor:{self.vendor_code}", f"type:{self.exception_type}", f"kind:{self.kind}"]

    def content(self) -> str:
        """Text sent to the memory backend: a machine header + the human-readable text."""
        return f"[MEMORYOPS {self.id}] {self.text}"

    def metadata(self) -> dict[str, str]:
        md = {k: str(v) for k, v in self.fields.items()}
        md.update(
            record_id=self.id,
            kind=self.kind,
            vendor_code=self.vendor_code,
            vendor_name=self.vendor_name,
            exception_type=self.exception_type,
            date=self.date,
        )
        return md


class RecallQuery(BaseModel):
    vendor_code: str
    vendor_name: str
    exception_type: str
    variance_pct: Optional[float] = None
    quantity_delta: Optional[float] = None
    extra: str = ""

    def to_text(self) -> str:
        parts = [f"How were {self.exception_type.replace('_', ' ')} exceptions from vendor {self.vendor_name} "
                 f"({self.vendor_code}) resolved?"]
        if self.variance_pct is not None:
            parts.append(f"Invoice is {self.variance_pct:+.2f}% versus PO price.")
        if self.quantity_delta is not None:
            parts.append(f"Invoiced quantity differs from received quantity by {self.quantity_delta:+g} units.")
        if self.extra:
            parts.append(self.extra)
        return " ".join(parts)


class RecalledMemory(BaseModel):
    id: str
    kind: str
    text: str
    score: float
    vendor_code: str = ""
    exception_type: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    source: str  # "hindsight" | "local"


class MemoryStatus(BaseModel):
    backend: Literal["hindsight", "local"]
    detail: str
    record_count: Optional[int] = None


class MemoryStore(ABC):
    name: str

    @abstractmethod
    def retain(self, record: MemoryRecord) -> None: ...

    def retain_many(self, records: list[MemoryRecord]) -> None:
        for r in records:
            self.retain(r)

    @abstractmethod
    def recall(self, query: RecallQuery, k: int = 5) -> list[RecalledMemory]: ...

    @abstractmethod
    def status(self) -> MemoryStatus: ...

    @abstractmethod
    def reset(self) -> None:
        """Forget everything (demo reset)."""
