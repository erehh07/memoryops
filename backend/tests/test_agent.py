import json
from types import SimpleNamespace as NS

from backend.agent.llm import GroqChooser
from backend.agent.recommender import Recommender
from backend.memory.base import MemoryStatus, MemoryStore, RecalledMemory

OPTIONS = [{"action": "approve", "label": "Approve", "amount": "1070.00"},
           {"action": "adjust", "label": "Adjust", "amount": "1000.00"},
           {"action": "reject", "label": "Reject", "amount": "0.00"},
           {"action": "escalate", "label": "Escalate", "amount": None}]


class StubMemory(MemoryStore):
    name = "stub"

    def __init__(self, items=None, fail=False):
        self.items, self.fail = items or [], fail

    def retain(self, record):
        pass

    def recall(self, query, k=5):
        if self.fail:
            raise ConnectionError("down")
        return self.items[:k]

    def status(self):
        return MemoryStatus(backend="local", detail="stub")

    def reset(self):
        pass


def prec(id, vendor="V01", etype="price_variance", decision="approved", mag="5.0", date="2026-06-10", who="Maria Chen"):
    return RecalledMemory(id=id, kind="decision", text=f"{decision} {mag}%", score=1.0, vendor_code=vendor,
                          exception_type=etype, source="local",
                          fields={"decision": decision, "magnitude": mag, "date": date, "reason": "fuel surcharge",
                                  "approver_name": who, "approver_role": "AP Manager"})


def recommend(r, mag=5.2, etype="price_variance"):
    return r.recommend(vendor_code="V01", vendor_name="Northwind", exc_type=etype, magnitude=mag, variance_pct=mag,
                       quantity_delta=None, case={"x": 1}, options=OPTIONS)


# ------------------------------------------------------------------ hard rule 3: no precedent -> escalate
def test_no_memories_escalates_and_cites_nothing():
    res = recommend(Recommender(StubMemory([]), None))
    assert res.action == "escalate" and res.cited_ids == [] and "No relevant precedent" in res.rationale


def test_only_other_vendor_memories_escalates():
    res = recommend(Recommender(StubMemory([prec("PREC-0001", vendor="V02"), prec("PREC-0002", etype="tax_error")]), None))
    assert res.action == "escalate" and res.cited_ids == []
    assert len(res.recalled) == 2 and not any(r["relevant"] for r in res.recalled)


def test_memory_failure_escalates():
    res = recommend(Recommender(StubMemory(fail=True), None))
    assert res.action == "escalate" and "recall failed" in res.rationale


# ------------------------------------------------------------------ offline chooser
def test_offline_follows_consistent_precedents_and_cites_them():
    mem = StubMemory([prec("PREC-0001", mag="5.1"), prec("PREC-0002", mag="4.4")])
    res = recommend(Recommender(mem, None))
    assert res.action == "approve" and res.amount == "1070.00"
    assert set(res.cited_ids) == {"PREC-0001", "PREC-0002"}
    assert res.suggested_approver == "Maria Chen (AP Manager)"


def test_offline_escalates_on_conflict():
    mem = StubMemory([prec("PREC-0001", mag="6.0"), prec("PREC-0002", mag="5.8", decision="rejected", date="2026-07-01")])
    res = recommend(Recommender(mem, None), mag=6.1)
    assert res.action == "escalate" and "conflict" in res.rationale


def test_offline_follows_changed_pattern():
    mem = StubMemory([prec("PREC-0001", mag="3.0", date="2026-06-01"), prec("PREC-0002", mag="3.0", date="2026-06-08"),
                      prec("PREC-0003", mag="3.0", decision="rejected", date="2026-07-02"),
                      prec("PREC-0004", mag="3.0", decision="rejected", date="2026-07-09")])
    res = recommend(Recommender(mem, None), mag=3.0)
    assert res.action == "reject" and "Pattern changed" in res.rationale
    assert set(res.cited_ids) == {"PREC-0003", "PREC-0004"}


def test_offline_escalates_beyond_approved_range():
    mem = StubMemory([prec("PREC-0001", mag="5.0"), prec("PREC-0002", mag="6.2")])
    res = recommend(Recommender(mem, None), mag=9.5)
    assert res.action == "escalate" and "exceeds" in res.rationale


def test_offline_uses_larger_adjusted_precedent():
    mem = StubMemory([prec("PREC-0001", mag="5.0"), prec("PREC-0002", mag="9.5", decision="adjusted")])
    res = recommend(Recommender(mem, None), mag=10.2)
    assert res.action == "adjust" and "PREC-0002" in res.cited_ids


# ------------------------------------------------------------------ LLM path (fake Groq client)
class FakeGroq:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return NS(choices=[NS(message=NS(content=r))])


def llm_json(**over):
    d = {"action": "approve", "cited_precedent_ids": ["PREC-0001"], "confidence": 0.8,
         "rationale": "Matches the usual fuel surcharge precedent.", "precedents_conflict": False}
    d.update(over)
    return json.dumps(d)


def chooser(responses):
    fake = FakeGroq(responses)
    return GroqChooser("k", "openai/gpt-oss-120b", "qwen/qwen3-32b", client=fake, sleep=lambda s: None), fake


def test_llm_valid_output_used_and_strict_schema_sent():
    ch, fake = chooser([llm_json()])
    res = recommend(Recommender(StubMemory([prec("PREC-0001")]), ch))
    assert res.action == "approve" and res.cited_ids == ["PREC-0001"] and res.engine == "groq:openai/gpt-oss-120b"
    fmt = fake.calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["properties"]["cited_precedent_ids"]["items"]["enum"] == ["PREC-0001"]


def test_llm_invented_citation_is_retried_then_falls_back_to_second_model():
    bad = llm_json(cited_precedent_ids=["PREC-9999"])
    ch, fake = chooser([bad, "not json", bad, "<think>hmm</think>" + llm_json()])
    res = recommend(Recommender(StubMemory([prec("PREC-0001")]), ch))
    assert res.action == "approve" and res.engine == "groq:qwen/qwen3-32b"
    assert fake.calls[3]["response_format"] == {"type": "json_object"}


def test_llm_total_failure_escalates_without_citations():
    ch, _ = chooser([TimeoutError()] * 6)
    res = recommend(Recommender(StubMemory([prec("PREC-0001")]), ch))
    assert res.action == "escalate" and res.cited_ids == [] and res.engine == "guard"


def test_llm_approve_without_citation_is_escalated():
    ch, _ = chooser([llm_json(cited_precedent_ids=[])])
    res = recommend(Recommender(StubMemory([prec("PREC-0001")]), ch))
    assert res.action == "escalate" and res.cited_ids == []


def test_llm_approve_beyond_range_is_guarded():
    ch, _ = chooser([llm_json()])
    res = recommend(Recommender(StubMemory([prec("PREC-0001", mag="5.0")]), ch), mag=12.0)
    assert res.action == "escalate" and "guard" in res.engine


def test_llm_cannot_cite_irrelevant_recalled_memory():
    # PREC-0002 was recalled but belongs to another vendor, so it is not in the allowed enum
    ch, fake = chooser([llm_json(cited_precedent_ids=["PREC-0001"])])
    recommend(Recommender(StubMemory([prec("PREC-0001"), prec("PREC-0002", vendor="V02")]), ch))
    enum = fake.calls[0]["response_format"]["json_schema"]["schema"]["properties"]["cited_precedent_ids"]["items"]["enum"]
    assert enum == ["PREC-0001"]
