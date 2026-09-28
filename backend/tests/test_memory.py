from types import SimpleNamespace as NS

from backend.memory.base import MemoryRecord, RecallQuery
from backend.memory.hindsight_store import HindsightMemoryStore
from backend.memory.local_store import LocalMemoryStore


def rec(id, vendor="V01", etype="price_variance", mag="5.0", decision="approved", date="2026-06-10", kind="decision"):
    return MemoryRecord(id=id, kind=kind, vendor_code=vendor, vendor_name=f"Vendor {vendor}", exception_type=etype,
                        text=f"{id}: {decision} {etype} for {vendor} fuel surcharge", date=date,
                        fields={"decision": decision, "magnitude": mag, "variance_pct": mag})


# ------------------------------------------------------------------ local fallback
def test_local_store_ranks_vendor_and_type_and_limits_k(tmp_path):
    m = LocalMemoryStore(str(tmp_path / "m.json"))
    m.retain(rec("PREC-0001", mag="5.0"))
    m.retain(rec("PREC-0002", mag="9.5", decision="adjusted"))
    m.retain(rec("PREC-0003", vendor="V02"))
    m.retain(rec("PREC-0004", etype="tax_error"))
    m.retain(rec("PREC-0005", vendor="V09", etype="tax_error"))  # neither vendor nor type -> never returned
    for i in range(6, 12):
        m.retain(rec(f"PREC-{i:04d}", mag=str(i)))
    q = RecallQuery(vendor_code="V01", vendor_name="Vendor V01", exception_type="price_variance", variance_pct=9.4)
    out = m.recall(q, k=5)
    assert len(out) == 5
    assert out[0].id == "PREC-0002"  # closest variance wins among full matches
    assert all(r.source == "local" for r in out)
    assert "PREC-0005" not in [r.id for r in m.recall(q, k=50)]


def test_local_store_persists_and_replaces_same_id(tmp_path):
    p = str(tmp_path / "m.json")
    m = LocalMemoryStore(p)
    m.retain(rec("VF-V01-price_variance", kind="vendor_fact"))
    m.retain(rec("VF-V01-price_variance", kind="vendor_fact", decision="rejected"))
    m2 = LocalMemoryStore(p)
    assert m2.status().record_count == 1 and m2.status().backend == "local"
    m2.reset()
    assert LocalMemoryStore(p).status().record_count == 0


# ------------------------------------------------------------------ Hindsight (fake client, real call shapes)
class FakeHindsight:
    def __init__(self, results, chunks=None):
        self.results, self.chunks = results, chunks or {}
        self.calls = []

    def get_version(self):
        return NS(api_version="0.10.1")

    def create_bank(self, bank_id, **kw):
        self.calls.append(("create_bank", bank_id, kw))

    def delete_bank(self, bank_id):
        self.calls.append(("delete_bank", bank_id))

    def retain(self, bank_id, content, **kw):
        self.calls.append(("retain", bank_id, content, kw))

    def retain_batch(self, bank_id, items, **kw):
        self.calls.append(("retain_batch", bank_id, items, kw))

    def recall(self, bank_id, query, **kw):
        self.calls.append(("recall", bank_id, query, kw))
        return NS(results=self.results, chunks=self.chunks)


def fact(id, text, document_id=None, metadata=None, chunk_id=None, score=0.5):
    return NS(id=id, text=text, document_id=document_id, metadata=metadata, chunk_id=chunk_id,
              scores=NS(final=score))


def test_hindsight_retain_sends_document_id_metadata_tags():
    fake = FakeHindsight([])
    hs = HindsightMemoryStore("http://x", None, "bank", client=fake)
    hs.connect()
    r = rec("PREC-0007")
    hs.retain(r)
    kind, bank, content, kw = fake.calls[-1]
    assert kind == "retain" and bank == "bank"
    assert content.startswith("[MEMORYOPS PREC-0007]")
    assert kw["document_id"] == "PREC-0007"
    assert kw["metadata"]["record_id"] == "PREC-0007" and kw["metadata"]["decision"] == "approved"
    assert all(isinstance(v, str) for v in kw["metadata"].values())
    assert set(kw["tags"]) == {"vendor:V01", "type:price_variance", "kind:decision"}
    hs.retain_many([rec("PREC-0008"), rec("PREC-0009")])
    assert fake.calls[-1][0] == "retain_batch" and len(fake.calls[-1][2]) == 2


def test_hindsight_recall_maps_facts_to_records_and_drops_unmappable():
    results = [
        fact("f1", "Maria approved a 5% surcharge", metadata={"record_id": "PREC-0001", "kind": "decision",
                                                              "vendor_code": "V01", "exception_type": "price_variance"},
             score=0.9),
        fact("f2", "another fact from the same decision", document_id="PREC-0001", score=0.8),
        fact("f3", "fact with only a document id", document_id="PREC-0002", score=0.7),
        fact("f4", "fact recovered via chunk", chunk_id="c1", score=0.6),
        fact("f5", "orphan fact with no id anywhere", score=0.95),
    ]
    chunks = {"c1": NS(id="c1", text="[MEMORYOPS VF-V01-price_variance] Vendor pattern ...", chunk_index=0)}
    fake = FakeHindsight(results, chunks)
    hs = HindsightMemoryStore("http://x", None, "bank", client=fake)
    q = RecallQuery(vendor_code="V01", vendor_name="Northwind", exception_type="price_variance", variance_pct=5.2)
    out = hs.recall(q, k=5)
    assert [r.id for r in out] == ["PREC-0001", "PREC-0002", "VF-V01-price_variance"]
    assert out[0].score == 0.9 and out[0].vendor_code == "V01"
    assert out[2].text.startswith("Vendor pattern")
    _, bank, query, kw = fake.calls[-1]
    assert kw["tags"] == ["vendor:V01", "type:price_variance"] and kw["tags_match"] == "any_strict"
    assert kw["include_chunks"] is True
    assert "Northwind" in query and "+5.20%" in query


def test_hindsight_recall_respects_k():
    results = [fact(f"f{i}", "x", document_id=f"PREC-{i:04d}") for i in range(9)]
    hs = HindsightMemoryStore("http://x", None, "bank", client=FakeHindsight(results))
    q = RecallQuery(vendor_code="V01", vendor_name="N", exception_type="price_variance")
    assert len(hs.recall(q, k=5)) == 5


def test_factory_falls_back_to_local_when_unconfigured(tmp_path):
    from backend.config import load_settings
    from backend.memory.factory import build_memory_store

    s = load_settings()
    object.__setattr__(s, "memory_backend", "auto")
    object.__setattr__(s, "local_memory_path", str(tmp_path / "m.json"))
    store = build_memory_store(s)
    assert store.name == "local" and "not configured" in store.status().detail


def test_factory_falls_back_to_local_when_unreachable(tmp_path):
    from backend.config import load_settings
    from backend.memory.factory import build_memory_store

    s = load_settings()
    object.__setattr__(s, "memory_backend", "auto")
    object.__setattr__(s, "hindsight_base_url", "http://127.0.0.1:9")  # nothing listens on the discard port
    object.__setattr__(s, "hindsight_timeout", 3.0)
    object.__setattr__(s, "local_memory_path", str(tmp_path / "m.json"))
    store = build_memory_store(s)
    assert store.name == "local" and "unreachable" in store.status().detail


def test_real_hindsight_client_builds_valid_requests():
    """Runs our store through the real hindsight-client code; only the HTTP layer is stubbed."""

    from hindsight_client import Hindsight
    from hindsight_client_api.models.recall_response import RecallResponse

    sent = {}

    async def fake_retain(bank_id, request, **kw):
        sent["retain"] = (bank_id, request.to_dict())
        return NS(success=True)

    async def fake_recall(bank_id, request, **kw):
        sent["recall"] = (bank_id, request.to_dict())
        return RecallResponse.from_dict({
            "results": [{"id": "f1", "text": "Maria approved 5%", "document_id": "PREC-0001",
                         "metadata": {"record_id": "PREC-0001", "kind": "decision", "vendor_code": "V01",
                                      "exception_type": "price_variance", "decision": "approved"},
                         "chunk_id": "c1", "scores": {"final": 0.8}}],
            "chunks": {"c1": {"id": "c1", "text": "[MEMORYOPS PREC-0001] On 2026-06-18 Maria approved", "chunk_index": 0}},
        })

    hs = HindsightMemoryStore.__new__(HindsightMemoryStore)
    import concurrent.futures
    hs._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    hs.base_url, hs.bank_id, hs.api_version = "http://unused", "bank", "?"
    hs.client = hs._call(lambda: Hindsight(base_url="http://unused", api_key="k"))
    hs.client._memory_api.retain_memories = fake_retain
    hs.client._memory_api.recall_memories = fake_recall

    hs.retain(rec("PREC-0001"))
    bank, body = sent["retain"]
    item = body["items"][0]
    assert bank == "bank" and item["document_id"] == "PREC-0001" and item["update_mode"] == "replace"
    assert item["content"].startswith("[MEMORYOPS PREC-0001]") and "vendor:V01" in item["tags"]

    out = hs.recall(RecallQuery(vendor_code="V01", vendor_name="N", exception_type="price_variance"))
    assert sent["recall"][1]["tags_match"] == "any_strict"
    assert out[0].id == "PREC-0001" and out[0].text.startswith("On 2026-06-18") and out[0].fields["decision"] == "approved"
    hs.close()
