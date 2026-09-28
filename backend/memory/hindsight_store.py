"""Hindsight integration — the ONLY file that touches the Hindsight client.

Verified against the installed `hindsight-client` 0.10.1 source (hindsight_client/hindsight_client.py):
  Hindsight(base_url, api_key=None, timeout=300.0)                       -> Bearer auth
  retain(bank_id, content, timestamp, context, document_id, metadata: dict[str,str],
         tags: list[str], update_mode='replace'|'append', retain_async=False)
  retain_batch(bank_id, items=[{content, timestamp, context, metadata, document_id, tags, update_mode}])
  recall(bank_id, query, types, max_tokens, budget, include_chunks, max_chunk_tokens,
         tags, tags_match='any'|'all'|'any_strict'|'all_strict'|'exact') -> RecallResponse
     RecallResponse.results: [RecallResult(id, text, type, document_id, metadata, chunk_id, tags, scores.final)]
     RecallResponse.chunks:  {chunk_id: ChunkData(id, text, chunk_index)}
  create_bank(bank_id, retain_mission=...)   (create-or-update)
  delete_bank(bank_id), get_version()

NOT confirmed without a live server (handled defensively below):
  * whether per-item `metadata` and `document_id` are always echoed back on each extracted fact.
    We map a recalled fact back to our record id by, in order: metadata["record_id"],
    document_id, the "[MEMORYOPS <id>]" header in the source chunk, the header in the fact text.
    A fact that cannot be mapped to a record id is dropped (never cited).
  * exact semantics of `update_mode='replace'` for re-retaining the same document_id.
"""
from __future__ import annotations

import concurrent.futures
import logging
from datetime import datetime

from .base import HEADER_RE, MemoryRecord, MemoryStatus, MemoryStore, RecallQuery, RecalledMemory

log = logging.getLogger("memoryops.hindsight")

_RETAIN_MISSION = (
    "This bank stores accounts-payable invoice exception resolutions. For each item, keep: the "
    "[MEMORYOPS <id>] marker, vendor, exception type, variance percent or quantity delta, the decision "
    "(approved/rejected/adjusted/escalated), approver name and role, the reason, and the date."
)
_CONTEXT = "Accounts payable: how a finance team resolved an invoice exception"


class HindsightMemoryStore(MemoryStore):
    name = "hindsight"

    def __init__(self, base_url: str, api_key: str | None, bank_id: str, timeout: float = 60.0, client=None):
        self.base_url = base_url
        self.bank_id = bank_id
        # The client's sync wrappers run an event loop per thread; keep every call on ONE thread so
        # the underlying HTTP session always sees the same loop (FastAPI runs sync routes in a pool).
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight")
        if client is None:
            from hindsight_client import Hindsight  # imported lazily so tests need no server

            client = self._call(lambda: Hindsight(base_url=base_url, api_key=api_key or None, timeout=timeout))
        self.client = client
        self.api_version = "unknown"

    def _call(self, fn):
        return self._pool.submit(fn).result()

    # -- lifecycle ---------------------------------------------------------------------------
    def connect(self) -> None:
        """Raises if the server is unreachable or auth fails."""
        v = self._call(self.client.get_version)
        self.api_version = getattr(v, "api_version", "unknown")
        self._call(lambda: self.client.create_bank(self.bank_id, retain_mission=_RETAIN_MISSION))

    def close(self) -> None:
        try:
            self._call(self.client.close)
        except Exception:
            pass
        self._pool.shutdown(wait=False)

    # -- MemoryStore -------------------------------------------------------------------------
    def _item(self, record: MemoryRecord) -> dict:
        return {
            "content": record.content(),
            "timestamp": datetime.fromisoformat(record.date[:10]),
            "context": _CONTEXT,
            "document_id": record.id,
            "metadata": record.metadata(),
            "tags": record.tags(),
            "update_mode": "replace",
        }

    def retain(self, record: MemoryRecord) -> None:
        item = self._item(record)
        self._call(
            lambda: self.client.retain(
                self.bank_id,
                item["content"],
                timestamp=item["timestamp"],
                context=item["context"],
                document_id=item["document_id"],
                metadata=item["metadata"],
                tags=item["tags"],
                update_mode="replace",
            )
        )

    def retain_many(self, records: list[MemoryRecord]) -> None:
        if not records:
            return
        items = [self._item(r) for r in records]
        self._call(lambda: self.client.retain_batch(self.bank_id, items))

    def recall(self, query: RecallQuery, k: int = 5) -> list[RecalledMemory]:
        resp = self._call(
            lambda: self.client.recall(
                self.bank_id,
                query.to_text(),
                types=["world", "experience"],
                max_tokens=2048,
                budget="mid",
                include_chunks=True,
                max_chunk_tokens=4000,
                tags=[f"vendor:{query.vendor_code}", f"type:{query.exception_type}"],
                tags_match="any_strict",
            )
        )
        chunks = getattr(resp, "chunks", None) or {}
        grouped: dict[str, RecalledMemory] = {}
        order: list[str] = []
        for res in resp.results or []:
            rid, md, raw = self._identify(res, chunks)
            if not rid:
                continue  # unmappable fact: never cite it
            score = float(res.scores.final) if getattr(res, "scores", None) else 0.0
            if rid in grouped:
                g = grouped[rid]
                g.score = max(g.score, score)
                if res.text not in g.text:
                    g.fields["_facts"] = (g.fields.get("_facts", "") + " | " + res.text).strip(" |")
                continue
            text = raw or res.text
            grouped[rid] = RecalledMemory(
                id=rid,
                kind=md.get("kind", "decision" if rid.startswith("PREC-") else "fact"),
                text=HEADER_RE.sub("", text).strip(),
                score=round(score, 4),
                vendor_code=md.get("vendor_code", ""),
                exception_type=md.get("exception_type", ""),
                fields={k2: v for k2, v in md.items() if k2 not in {"record_id"}},
                source="hindsight",
            )
            order.append(rid)
        return [grouped[r] for r in order][:k]

    def _identify(self, res, chunks) -> tuple[str | None, dict, str | None]:
        md = dict(getattr(res, "metadata", None) or {})
        raw = None
        chunk_id = getattr(res, "chunk_id", None)
        if chunk_id and chunk_id in chunks:
            raw = getattr(chunks[chunk_id], "text", None)
        rid = md.get("record_id")
        if not rid and getattr(res, "document_id", None) and HEADER_RE.fullmatch(f"[MEMORYOPS {res.document_id}]"):
            rid = res.document_id
        for candidate in (raw, getattr(res, "text", "")):
            if not rid and candidate:
                m = HEADER_RE.search(candidate)
                if m:
                    rid = m.group("id")
        # A chunk may hold the header of a *different* record only if Hindsight merged documents;
        # document_id wins over chunk text when both exist.
        if raw and rid and f"[MEMORYOPS {rid}]" not in raw:
            raw = None
        return rid, md, raw

    def status(self) -> MemoryStatus:
        return MemoryStatus(
            backend="hindsight",
            detail=f"Hindsight {self.api_version} at {self.base_url}, bank '{self.bank_id}'",
        )

    def reset(self) -> None:
        try:
            self._call(lambda: self.client.delete_bank(self.bank_id))
        except Exception as e:  # bank may not exist yet
            log.warning("delete_bank failed: %s", type(e).__name__)
        self._call(lambda: self.client.create_bank(self.bank_id, retain_mission=_RETAIN_MISSION))
