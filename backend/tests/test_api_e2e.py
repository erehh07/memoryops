"""End-to-end through the HTTP API: seed -> process -> replay seniors -> learning is measurable."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def test_status_uses_local_fallback_and_hides_secrets(client):
    s = client.get("/api/status").json()
    assert s["memory"]["backend"] == "local"
    assert s["llm"]["mode"] == "offline"
    assert "groq_api_key" not in s["settings"] and "hindsight_api_key" not in s["settings"]


def test_seed_shape(client):
    b = client.get("/api/batches").json()
    assert [x["invoices"] for x in b] == [30, 30, 30]
    assert len(client.get("/api/vendors").json()) == 10


def test_learning_curve(client):
    r1 = client.post("/api/batches/1/process").json()
    assert r1["exceptions"] >= 15
    m = {x["batch_id"]: x for x in client.get("/api/batches").json()}
    assert m[1]["escalation_rate"] == 1.0  # empty memory: must escalate everything

    rep = client.post("/api/replay-seniors", json={"batch_id": 1}).json()
    assert rep["applied"] == r1["exceptions"] and rep["no_senior_decision"] == []

    client.post("/api/batches/2/process")
    client.post("/api/replay-seniors", json={"batch_id": 2})
    client.post("/api/batches/3/process")
    m = {x["batch_id"]: x for x in client.get("/api/batches").json()}
    assert m[2]["escalation_rate"] <= 0.25
    assert m[3]["escalation_rate"] <= 0.25
    assert m[2]["with_citations"] == m[2]["exceptions"]


def test_batch3_hard_cases(client):
    excs = {(e["invoice"]["number"], e["type"]): e for e in client.get("/api/batches/3/exceptions").json()}
    reco = lambda k: excs[k]["recommendation"]
    assert reco(("VLI-0012", "price_variance"))["action"] == "escalate"          # no history
    assert reco(("VLI-0012", "price_variance"))["cited_ids"] == []
    assert reco(("SOL-4451", "price_variance"))["action"] == "escalate"          # conflicting precedents
    assert reco(("ARB-1120", "price_variance"))["action"] == "reject"            # quirk changed
    assert reco(("KIT-9080", "missing_po"))["action"] == "escalate"              # way above precedent
    assert reco(("NFF-24172", "price_variance"))["action"] == "adjust"           # above the 8% cap
    assert reco(("NFF-24160", "price_variance"))["action"] == "approve"
    assert reco(("CRS-50377-1", "duplicate_invoice"))["action"] == "reject"
    assert "Priya Raman" in (reco(("HPP-88050", "price_variance"))["suggested_approver"] or "")


def test_every_citation_was_recalled(client):
    for b in (1, 2, 3):
        for e in client.get(f"/api/batches/{b}/exceptions").json():
            r = e["recommendation"]
            recalled_relevant = {x["id"] for x in r["recalled"] if x["relevant"]}
            assert set(r["cited_ids"]) <= recalled_relevant
            if r["action"] != "escalate":
                assert r["cited_ids"]


def test_human_decision_is_retained_and_override_flagged(client):
    e = next(x for x in client.get("/api/batches/3/exceptions").json() if x["invoice"]["number"] == "NFF-24160")
    assert e["recommendation"]["action"] == "approve"
    bad = client.post(f"/api/exceptions/{e['id']}/decide", json={"decision": "rejected", "approver_name": "",
                                                                 "approver_role": "x", "reason": "no"})
    assert bad.status_code == 422
    d = client.post(f"/api/exceptions/{e['id']}/decide",
                    json={"decision": "rejected", "approver_name": "Maria Chen", "approver_role": "AP Manager",
                          "reason": "Testing an override: surcharge not supported by the lane tariff"}).json()
    assert d["decision"]["is_override"] is True and d["decision"]["retained"] is True
    again = client.post(f"/api/exceptions/{e['id']}/decide",
                        json={"decision": "approved", "approver_name": "Maria Chen", "approver_role": "AP Manager",
                              "reason": "second try"})
    assert again.status_code == 422
    rid = d["decision"]["memory_record_id"]
    hits = client.get("/api/memory/recall", params={"vendor_code": "V01", "exception_type": "price_variance",
                                                     "variance_pct": 5.3}).json()["results"]
    assert rid in [h["id"] for h in hits]
    assert any(a["action"] == "decided" for a in client.get("/api/audit").json())


def test_recall_returns_at_most_five(client):
    hits = client.get("/api/memory/recall", params={"vendor_code": "V01", "exception_type": "price_variance"}).json()
    assert 0 < len(hits["results"]) <= 5


def test_forbidden_word_absent_from_repo():
    word = "hack" + "athon"
    skip = {".venv", "node_modules", ".git", "dist", "var", "__pycache__", ".pytest_cache"}
    offenders = []
    for p in ROOT.rglob("*"):
        if any(part in skip for part in p.parts) or not p.is_file() or p.suffix in {".png", ".ico", ".db"}:
            continue
        try:
            if re.search(word, p.read_text(encoding="utf-8", errors="ignore"), re.I):
                offenders.append(str(p))
        except OSError:
            pass
    assert offenders == []
