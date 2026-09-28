"""MemoryOps API. Run: uvicorn backend.app.main:app --reload --port 8000

The agent recommends only. Decisions are recorded, never paid or posted.
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services as S
from ..agent.llm import GroqChooser
from ..agent.recommender import Recommender
from ..config import settings
from ..data.seed import seed
from ..db import (AuditLog, Decision, ExceptionRow, Invoice, Recommendation, SessionLocal, Vendor, engine,
                  init_db)
from ..memory.base import MemoryStore, RecallQuery
from ..memory.factory import build_memory_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("memoryops")


class State:
    memory: MemoryStore
    recommender: Recommender
    lock = threading.RLock()  # one writer at a time (SQLite + sequential memory writes)


state = State()


def build_state() -> None:
    state.memory = build_memory_store(settings)
    llm = None
    if settings.groq_api_key:
        llm = GroqChooser(settings.groq_api_key, settings.groq_model, settings.groq_fallback_model)
    state.recommender = Recommender(state.memory, llm, enrich=S.enrich_precedent(SessionLocal))


def ensure_seeded() -> None:
    init_db()
    with SessionLocal() as db:
        if db.query(Vendor).count() == 0:
            log.info("Empty database: seeding synthetic data %s", seed(engine))


@asynccontextmanager
async def lifespan(_app):
    ensure_seeded()
    build_state()
    yield


app = FastAPI(title="MemoryOps", lifespan=lifespan,
              description="Accounts-payable exception agent with persistent memory. Recommends only.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------ serializers
def ser_reco(r: Optional[Recommendation]) -> Optional[dict]:
    if r is None:
        return None
    return {"id": r.id, "action": r.action, "amount": S._s(r.amount), "confidence": r.confidence,
            "rationale": r.rationale, "cited_ids": r.cited_ids, "recalled": r.recalled,
            "suggested_approver": r.suggested_approver, "engine": r.engine, "memory_backend": r.memory_backend,
            "created_at": r.created_at}


def ser_decision(d: Optional[Decision]) -> Optional[dict]:
    if d is None:
        return None
    return {"id": d.id, "decision": d.decision, "amount": S._s(d.amount), "approver_name": d.approver_name,
            "approver_role": d.approver_role, "reason": d.reason, "decided_on": d.decided_on,
            "is_override": d.is_override, "source": d.source, "memory_record_id": d.memory_record_id,
            "retained": d.retained}


def ser_exception(db: Session, e: ExceptionRow, full: bool = False) -> dict:
    inv, v = e.invoice, e.invoice.vendor
    d = db.scalars(select(Decision).where(Decision.exception_id == e.id)).first()
    out = {
        "id": e.id, "type": e.type, "status": e.status, "variance_pct": S._s(e.variance_pct),
        "quantity_delta": S._s(e.quantity_delta), "magnitude": S._s(e.magnitude), "details": e.details,
        "invoice": {"id": inv.id, "number": inv.invoice_number, "date": inv.invoice_date, "po_number": inv.po_number,
                    "currency": inv.currency, "subtotal": str(inv.subtotal), "tax": str(inv.tax_amount),
                    "total": str(inv.total), "batch_id": inv.batch_id},
        "vendor": {"code": v.code, "name": v.name},
        "recommendation": ser_reco(S.latest_recommendation(db, e.id)),
        "decision": ser_decision(d),
    }
    if full:
        po, grs = S._context(db, inv)
        out["invoice"]["lines"] = inv.lines
        out["vendor"].update(payment_terms=v.payment_terms, currency=v.currency, tax_label=v.tax_label,
                             tax_rate=str(v.tax_rate))
        out["po"] = {"po_number": po.po_number, "order_date": po.order_date, "lines": po.lines} if po else None
        out["receipts"] = [{"gr_number": g.gr_number, "received_date": g.received_date, "lines": g.lines} for g in grs]
        out["options"] = S.exception_options(db, e)
        out["history"] = [ser_reco(r) for r in db.scalars(select(Recommendation).where(
            Recommendation.exception_id == e.id).order_by(Recommendation.id)).all()]
    return out


# ------------------------------------------------------------------ routes
@app.get("/api/status")
def status():
    ms = state.memory.status()
    llm = state.recommender.llm
    return {
        "memory": ms.model_dump(),
        "llm": {"mode": "groq" if llm else "offline", "models": llm.models if llm else [],
                "detail": "Groq structured output" if llm else "No GROQ_API_KEY: deterministic precedent matcher"},
        "settings": settings.redacted(),
        "policy": "Recommendations only. A human approves every decision. Nothing is paid or posted.",
    }


@app.get("/api/batches")
def batches(db: Session = Depends(get_db)):
    return S.metrics(db)


@app.post("/api/batches/{batch_id}/process")
def process(batch_id: int, db: Session = Depends(get_db)):
    with state.lock:
        try:
            return S.process_batch(db, batch_id, state.recommender)
        except KeyError as e:
            raise HTTPException(404, str(e))


@app.post("/api/batches/{batch_id}/recommend")
def rerecommend(batch_id: int, db: Session = Depends(get_db)):
    with state.lock:
        return S.recommend_pending(db, batch_id, state.recommender)


@app.get("/api/batches/{batch_id}/invoices")
def batch_invoices(batch_id: int, db: Session = Depends(get_db)):
    invs = db.scalars(select(Invoice).where(Invoice.batch_id == batch_id).order_by(Invoice.id)).all()
    return [{"id": i.id, "number": i.invoice_number, "vendor": i.vendor.name, "vendor_code": i.vendor_code,
             "po_number": i.po_number, "date": i.invoice_date, "currency": i.currency, "total": str(i.total),
             "status": i.status} for i in invs]


@app.get("/api/batches/{batch_id}/exceptions")
def batch_exceptions(batch_id: int, db: Session = Depends(get_db)):
    excs = db.scalars(select(ExceptionRow).join(Invoice).where(Invoice.batch_id == batch_id)
                      .order_by(ExceptionRow.id)).all()
    return [ser_exception(db, e) for e in excs]


@app.get("/api/exceptions/{exception_id}")
def exception_detail(exception_id: int, db: Session = Depends(get_db)):
    e = db.get(ExceptionRow, exception_id)
    if e is None:
        raise HTTPException(404, "exception not found")
    return ser_exception(db, e, full=True)


@app.post("/api/exceptions/{exception_id}/recommend")
def exception_recommend(exception_id: int, db: Session = Depends(get_db)):
    e = db.get(ExceptionRow, exception_id)
    if e is None:
        raise HTTPException(404, "exception not found")
    if e.status == "decided":
        raise HTTPException(409, "already decided")
    with state.lock:
        S.recommend_exception(db, e, state.recommender)
    return ser_exception(db, e, full=True)


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected", "adjusted", "escalated"]
    approver_name: str = Field(min_length=2, max_length=120)
    approver_role: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=5, max_length=2000)


@app.post("/api/exceptions/{exception_id}/decide")
def exception_decide(exception_id: int, body: DecisionIn, db: Session = Depends(get_db)):
    with state.lock:
        try:
            S.decide(db, state.memory, exception_id, body.decision, body.approver_name, body.approver_role, body.reason)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
    return ser_exception(db, db.get(ExceptionRow, exception_id), full=True)


class ReplayIn(BaseModel):
    batch_id: int


@app.post("/api/replay-seniors")
def replay(body: ReplayIn, db: Session = Depends(get_db)):
    with state.lock:
        try:
            return S.replay_seniors(db, state.memory, body.batch_id)
        except ValueError as e:
            raise HTTPException(409, str(e))


@app.get("/api/senior-decisions")
def senior_decisions():
    return S.load_senior_file()


@app.get("/api/memory/recall")
def memory_recall(vendor_code: str, exception_type: str, variance_pct: Optional[float] = None,
                  db: Session = Depends(get_db)):
    v = db.get(Vendor, vendor_code)
    if v is None:
        raise HTTPException(404, "vendor not found")
    q = RecallQuery(vendor_code=v.code, vendor_name=v.name, exception_type=exception_type, variance_pct=variance_pct)
    try:
        res = state.memory.recall(q, k=5)
    except Exception as e:
        raise HTTPException(503, f"memory recall failed: {type(e).__name__}")
    return {"query": q.to_text(), "backend": state.memory.name, "results": [r.model_dump() for r in res]}


@app.get("/api/vendors")
def vendors(db: Session = Depends(get_db)):
    return [{"code": v.code, "name": v.name, "payment_terms": v.payment_terms, "currency": v.currency,
             "tax_label": v.tax_label, "tax_rate": str(v.tax_rate)} for v in db.scalars(select(Vendor)).all()]


@app.get("/api/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))).all()
    return [{"id": a.id, "ts": a.ts, "actor": a.actor, "action": a.action, "entity": a.entity,
             "entity_id": a.entity_id, "details": a.details} for a in rows]


@app.post("/api/reset")
def reset():
    """Demo reset: re-seed SQLite and wipe the memory bank."""
    with state.lock:
        counts = seed(engine)
        state.memory.reset()
    return {"reseeded": counts, "memory": state.memory.status().model_dump()}


@app.post("/api/memory/reconnect")
def reconnect():
    with state.lock:
        build_state()
    return state.memory.status().model_dump()


# ------------------------------------------------------------------ built frontend (optional)
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(DIST / "index.html")
