"""Application services: batch processing, recommendations, human decisions, memory writes, metrics.

Nothing here pays or posts anything. A decision is only a recorded human judgement.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent.recommender import Recommender
from .config import settings
from .db import (Batch, Decision, ExceptionRow, GoodsReceipt, Invoice, PurchaseOrder, Recommendation, Vendor,
                 audit, utcnow)
from .detector.match import InvoiceDoc, Line, PODoc, ReceiptDoc, detect
from .detector.options import ACTION_TO_DECISION, DECISION_TO_ACTION, build_options
from .memory.base import MemoryRecord, MemoryStore

log = logging.getLogger("memoryops.services")
SENIOR_FILE = Path(__file__).parent / "data" / "senior_decisions.json"
DECISIONS = ("approved", "rejected", "adjusted", "escalated")


# ---------------------------------------------------------------- documents
def _inv_doc(i: Invoice) -> InvoiceDoc:
    return InvoiceDoc(i.id, i.invoice_number, i.vendor_code, i.po_number, i.invoice_date,
                      [Line.of(l) for l in i.lines], i.subtotal, i.tax_amount, i.total)


def _context(db: Session, inv: Invoice):
    po = db.get(PurchaseOrder, inv.po_number) if inv.po_number else None
    grs = db.scalars(select(GoodsReceipt).where(GoodsReceipt.po_number == inv.po_number)).all() if po else []
    return po, grs


# ---------------------------------------------------------------- processing
def process_batch(db: Session, batch_id: int, rec: Recommender) -> dict:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise KeyError(f"batch {batch_id} not found")
    if batch.status == "processed":
        return {"batch_id": batch_id, "already_processed": True, **recommend_pending(db, batch_id, rec)}
    invoices = db.scalars(select(Invoice).where(Invoice.batch_id == batch_id).order_by(Invoice.id)).all()
    n_exc = 0
    for inv in invoices:
        earlier = db.scalars(select(Invoice).where(Invoice.vendor_code == inv.vendor_code, Invoice.id < inv.id)).all()
        po, grs = _context(db, inv)
        found = detect(
            _inv_doc(inv),
            PODoc(po.po_number, po.vendor_code, [Line.of(l) for l in po.lines]) if po else None,
            [ReceiptDoc(g.po_number, [Line.of(l) for l in g.lines]) for g in grs],
            inv.vendor.tax_rate,
            [_inv_doc(e) for e in earlier],
            settings.price_tolerance_pct,
        )
        inv.status = "exception" if found else "matched"
        for f in found:
            db.add(ExceptionRow(invoice_id=inv.id, type=f.type, variance_pct=f.variance_pct,
                                quantity_delta=f.quantity_delta, magnitude=f.magnitude, details=f.details))
            n_exc += 1
    batch.status = "processed"
    batch.processed_at = utcnow()
    audit(db, "system", "batch_processed", "batch", batch_id, invoices=len(invoices), exceptions=n_exc)
    db.commit()
    r = recommend_pending(db, batch_id, rec)
    return {"batch_id": batch_id, "invoices": len(invoices), "exceptions": n_exc, **r}


def recommend_pending(db: Session, batch_id: int, rec: Recommender) -> dict:
    excs = db.scalars(select(ExceptionRow).join(Invoice).where(Invoice.batch_id == batch_id,
                                                               ExceptionRow.status != "decided")).all()
    for e in excs:
        recommend_exception(db, e, rec)
    return {"recommended": len(excs)}


def exception_options(db: Session, e: ExceptionRow) -> list[dict]:
    inv = e.invoice
    po, grs = _context(db, inv)
    received: dict[str, Decimal] = defaultdict(Decimal)
    for g in grs:
        for l in g.lines:
            received[l["sku"]] += Decimal(str(l["qty_received"]))
    return build_options(e.type, e.details, {"total": inv.total, "subtotal": inv.subtotal, "lines": inv.lines},
                         po.lines if po else None, received, inv.vendor.tax_rate)


def case_summary(e: ExceptionRow) -> dict:
    inv, v = e.invoice, e.invoice.vendor
    return {
        "exception_type": e.type,
        "vendor": {"code": v.code, "name": v.name, "payment_terms": v.payment_terms, "currency": v.currency},
        "invoice": {"number": inv.invoice_number, "date": inv.invoice_date, "po_number": inv.po_number,
                    "subtotal": str(inv.subtotal), "tax": str(inv.tax_amount), "total": str(inv.total)},
        "measured": {"variance_pct": _s(e.variance_pct), "quantity_delta": _s(e.quantity_delta),
                     "magnitude": _s(e.magnitude), **e.details},
    }


def _s(v) -> Optional[str]:
    return None if v is None else str(v)


def _f(v) -> Optional[float]:
    return None if v is None else float(v)


def recommend_exception(db: Session, e: ExceptionRow, rec: Recommender) -> Recommendation:
    inv = e.invoice
    res = rec.recommend(
        vendor_code=inv.vendor_code, vendor_name=inv.vendor.name, exc_type=e.type, magnitude=_f(e.magnitude),
        variance_pct=_f(e.variance_pct), quantity_delta=_f(e.quantity_delta), case=case_summary(e),
        options=exception_options(db, e),
    )
    row = Recommendation(
        exception_id=e.id, action=res.action, amount=Decimal(res.amount) if res.amount else None,
        confidence=res.confidence, rationale=res.rationale, cited_ids=res.cited_ids, recalled=res.recalled,
        suggested_approver=res.suggested_approver, engine=res.engine + (f" | {'; '.join(res.notes)}" if res.notes else ""),
        memory_backend=rec.memory.name,
    )
    db.add(row)
    e.status = "recommended"
    db.flush()
    audit(db, "agent", "recommended", "exception", e.id, action=res.action, cited=res.cited_ids, engine=res.engine)
    db.commit()
    return row


def latest_recommendation(db: Session, exception_id: int) -> Optional[Recommendation]:
    return db.scalars(select(Recommendation).where(Recommendation.exception_id == exception_id)
                      .order_by(Recommendation.id.desc()).limit(1)).first()


# ---------------------------------------------------------------- decisions + memory
def decide(db: Session, memory: MemoryStore, exception_id: int, decision: str, approver_name: str,
           approver_role: str, reason: str, decided_on: Optional[str] = None, source: str = "human") -> Decision:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not approver_name.strip() or not approver_role.strip() or len(reason.strip()) < 5:
        raise ValueError("approver name, role and a reason (5+ chars) are required")
    e = db.get(ExceptionRow, exception_id)
    if e is None:
        raise KeyError(f"exception {exception_id} not found")
    if e.status == "decided":
        raise ValueError("exception already decided")
    reco = latest_recommendation(db, exception_id)
    opts = {o["action"]: o["amount"] for o in exception_options(db, e)}
    action = DECISION_TO_ACTION[decision]
    if action not in opts:
        raise ValueError(f"'{decision}' is not an available option for {e.type}")
    d = Decision(
        exception_id=e.id, recommendation_id=reco.id if reco else None, decision=decision,
        amount=Decimal(opts[action]) if opts[action] else None, approver_name=approver_name.strip(),
        approver_role=approver_role.strip(), reason=reason.strip(),
        decided_on=decided_on or date.today().isoformat(),
        is_override=bool(reco and reco.action != "escalate" and reco.action != action), source=source,
    )
    db.add(d)
    e.status = "decided"
    db.flush()
    d.memory_record_id = f"PREC-{d.id:04d}"
    others_open = db.scalars(select(ExceptionRow).where(ExceptionRow.invoice_id == e.invoice_id,
                                                        ExceptionRow.status != "decided")).first()
    if not others_open:
        e.invoice.status = "resolved"
    audit(db, f"{d.approver_name} ({d.approver_role})", "decided", "exception", e.id, decision=decision,
          override=d.is_override, source=source, note="recorded only; nothing paid or posted")
    db.commit()
    retain_decision(db, memory, d)
    return d


def decision_record(d: Decision) -> MemoryRecord:
    e, inv = d.exception, d.exception.invoice
    v = inv.vendor
    size = ""
    if e.variance_pct is not None:
        size = f" of {e.variance_pct:+}% versus PO price"
    elif e.quantity_delta is not None:
        size = f" of {e.quantity_delta:+} units invoiced versus received ({e.magnitude}% of received)"
    elif e.type == "tax_error":
        size = f" (charged {e.details.get('implied_rate_pct')}% vs contracted {e.details.get('expected_rate_pct')}%)"
    elif e.type == "missing_po":
        size = f" for {inv.currency} {inv.total} with no PO"
    elif e.type == "duplicate_invoice":
        size = f" (duplicate of {e.details.get('duplicate_of_number')}, {e.details.get('match_rule')})"
    override = " This overrode the agent's recommendation." if d.is_override else ""
    text = (f"On {d.decided_on}, {d.approver_name} ({d.approver_role}) {d.decision} invoice {inv.invoice_number} "
            f"from {v.name} ({v.code}): {e.type.replace('_', ' ')}{size}. Reason: {d.reason}.{override}")
    return MemoryRecord(
        id=d.memory_record_id, kind="decision", vendor_code=v.code, vendor_name=v.name, exception_type=e.type,
        text=text, date=d.decided_on,
        fields={
            "invoice_id": inv.invoice_number, "decision": d.decision, "approver_name": d.approver_name,
            "approver_role": d.approver_role, "reason": d.reason, "is_override": str(d.is_override).lower(),
            "variance_pct": _s(e.variance_pct) or "", "quantity_delta": _s(e.quantity_delta) or "",
            "magnitude": _s(e.magnitude) or "", "amount": _s(d.amount) or "", "currency": inv.currency,
        },
    )


def retain_decision(db: Session, memory: MemoryStore, d: Decision, with_facts: bool = True) -> None:
    records = [decision_record(d)]
    if with_facts:
        records += derived_facts(db, d.exception.invoice.vendor_code, d.exception.type)
    try:
        memory.retain_many(records)
        d.retained = True
        audit(db, "system", "retained", "decision", d.id, record_ids=[r.id for r in records], backend=memory.name)
    except Exception as ex:
        audit(db, "system", "retain_failed", "decision", d.id, error=type(ex).__name__)
        log.warning("retain failed for %s: %s", d.memory_record_id, type(ex).__name__)
    db.commit()


def derived_facts(db: Session, vendor_code: str, exc_type: str) -> list[MemoryRecord]:
    """Vendor-level facts and approver preferences, rebuilt deterministically once decisions repeat."""
    rows = db.scalars(select(Decision).join(ExceptionRow).join(Invoice)
                      .where(Invoice.vendor_code == vendor_code, ExceptionRow.type == exc_type)
                      .order_by(Decision.decided_on, Decision.id)).all()
    if len(rows) < 2:
        return []
    v = db.get(Vendor, vendor_code)
    label = exc_type.replace("_", " ")
    by_dec: dict[str, list[Decision]] = defaultdict(list)
    for r in rows:
        by_dec[r.decision].append(r)
    parts = []
    for dec, ds in sorted(by_dec.items()):
        mags = [float(x.exception.magnitude) for x in ds if x.exception.magnitude is not None]
        rng = f", size {min(mags):g}-{max(mags):g}" if mags else ""
        parts.append(f"{dec} {len(ds)}x ({ds[0].decided_on} to {ds[-1].decided_on}{rng})")
    last = rows[-1]
    approved_mags = [float(x.exception.magnitude) for x in by_dec.get("approved", []) if x.exception.magnitude is not None]
    text = (f"Vendor pattern for {v.name} ({v.code}), {label}: {len(rows)} past decisions: {'; '.join(parts)}. "
            f"Most recent: {last.decision} on {last.decided_on} by {last.approver_name} - \"{last.reason}\". "
            f"Vendor terms: {v.payment_terms}, {v.currency}, {v.tax_label} {Decimal(v.tax_rate) * 100:g}%.")
    fields = {"count": str(len(rows)), "latest_decision": last.decision, "latest_date": last.decided_on,
              "date": last.decided_on}
    if approved_mags:
        fields["approved_max"] = f"{max(approved_mags):g}"
        fields["approved_min"] = f"{min(approved_mags):g}"
    out = [MemoryRecord(id=f"VF-{vendor_code}-{exc_type}", kind="vendor_fact", vendor_code=vendor_code,
                        vendor_name=v.name, exception_type=exc_type, text=text, date=last.decided_on, fields=fields)]
    by_person: dict[tuple, list[Decision]] = defaultdict(list)
    for r in rows:
        by_person[(r.approver_name, r.approver_role)].append(r)
    for (name, role), ds in by_person.items():
        if len(ds) < 2:
            continue
        decs = {x.decision for x in ds}
        sole = len(by_person) == 1
        slug = re.sub(r"[^A-Za-z0-9]", "", name)[:20]
        text = (f"Approver preference: {name} ({role}) decided {len(ds)} {label} exceptions for {v.name} ({v.code}); "
                f"outcomes: {', '.join(sorted(decs))}. Latest reason: \"{ds[-1].reason}\"."
                + (f" All {len(ds)} decisions for this vendor and exception type were made by {name}; route to them."
                   if sole else ""))
        f = {"approver_name": name, "approver_role": role, "count": str(len(ds)), "date": ds[-1].decided_on}
        if sole:
            f["required_approver"] = f"{name} ({role})"
        out.append(MemoryRecord(id=f"AP-{slug}-{vendor_code}-{exc_type}", kind="approver_pref", vendor_code=vendor_code,
                                vendor_name=v.name, exception_type=exc_type, text=text, date=ds[-1].decided_on,
                                fields=f))
    return out


def enrich_precedent(db_factory):
    """Returns fields for a PREC-<id> record from SQLite (used only if the memory backend drops metadata)."""
    def _enrich(record_id: str) -> Optional[dict]:
        m = re.fullmatch(r"PREC-(\d+)", record_id)
        if not m:
            return None
        with db_factory() as db:
            d = db.get(Decision, int(m.group(1)))
            if d is None:
                return None
            r = decision_record(d)
            return {**r.fields, "vendor_code": r.vendor_code, "exception_type": r.exception_type, "date": r.date}
    return _enrich


# ---------------------------------------------------------------- replay
def load_senior_file() -> list[dict]:
    return json.loads(SENIOR_FILE.read_text(encoding="utf-8"))["decisions"]


def replay_seniors(db: Session, memory: MemoryStore, batch_id: int) -> dict:
    """Applies the pre-written senior decisions to still-open exceptions in a processed batch."""
    batch = db.get(Batch, batch_id)
    if batch is None or batch.status != "processed":
        raise ValueError("process the batch first")
    wanted = {(s["invoice_number"], s["exception_type"]): s for s in load_senior_file()}
    excs = db.scalars(select(ExceptionRow).join(Invoice).where(Invoice.batch_id == batch_id)
                      .order_by(ExceptionRow.id)).all()
    applied, skipped, missing = 0, 0, []
    for e in excs:
        s = wanted.get((e.invoice.invoice_number, e.type))
        if e.status == "decided":
            skipped += 1
            continue
        if s is None:
            missing.append(f"{e.invoice.invoice_number}/{e.type}")
            continue
        decide(db, memory, e.id, s["decision"], s["approver_name"], s["approver_role"], s["reason"],
               decided_on=s["decided_on"], source="replay")
        applied += 1
    audit(db, "system", "replayed_seniors", "batch", batch_id, applied=applied, skipped=skipped, missing=missing)
    db.commit()
    return {"applied": applied, "already_decided": skipped, "no_senior_decision": missing}


# ---------------------------------------------------------------- metrics
def metrics(db: Session) -> list[dict]:
    out = []
    for b in db.scalars(select(Batch).order_by(Batch.id)).all():
        excs = db.scalars(select(ExceptionRow).join(Invoice).where(Invoice.batch_id == b.id)).all()
        n_inv = db.query(Invoice).filter(Invoice.batch_id == b.id).count()
        first_recos, decided, agree, overrides, graded = [], 0, 0, 0, 0
        by_type: dict[str, int] = defaultdict(int)
        for e in excs:
            by_type[e.type] += 1
            # first recommendation = what the agent said when the batch arrived
            r = db.scalars(select(Recommendation).where(Recommendation.exception_id == e.id)
                           .order_by(Recommendation.id)).first()
            if r:
                first_recos.append(r)
            d = db.scalars(select(Decision).where(Decision.exception_id == e.id)).first()
            if d:
                decided += 1
                overrides += int(d.is_override)
                last = db.get(Recommendation, d.recommendation_id) if d.recommendation_id else None
                if last and last.action != "escalate":  # escalations hand off; they are not graded
                    graded += 1
                    agree += int(ACTION_TO_DECISION[last.action] == d.decision)
        n_reco = len(first_recos)
        esc = sum(1 for r in first_recos if r.action == "escalate")
        out.append({
            "batch_id": b.id, "name": b.name, "status": b.status, "invoices": n_inv, "exceptions": len(excs),
            "exceptions_by_type": dict(by_type), "recommended": n_reco, "escalated": esc,
            "escalation_rate": round(esc / n_reco, 3) if n_reco else None,
            "decided": decided, "agreed": agree, "overrides": overrides,
            "graded": graded, "agreement_rate": round(agree / graded, 3) if graded else None,
            "with_citations": sum(1 for r in first_recos if r.cited_ids),
        })
    return out
