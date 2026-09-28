"""Recommender: exception + recalled precedents -> validated recommendation.

Pipeline (hard rules enforced here, in code, regardless of which chooser runs):
  1. recall <= 5 memories for (vendor, exception type, size)
  2. keep only RELEVANT ones: same vendor AND same exception type
  3. none relevant -> escalate, say so, cite nothing
  4. chooser (Groq LLM, or offline deterministic) picks one pre-computed option
  5. validate: cited IDs must be a subset of recalled relevant IDs; a non-escalate action must cite
     at least one precedent; approvals larger than any approved precedent are escalated
  6. any failure -> escalate
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..memory.base import MemoryStore, RecallQuery, RecalledMemory
from . import offline
from .llm import GroqChooser

MAX_PRECEDENTS = 5


@dataclass
class RecommendationResult:
    action: str
    amount: Optional[str]
    confidence: float
    rationale: str
    cited_ids: list[str]
    recalled: list[dict]  # every recalled memory with a 'relevant' flag (for transparency)
    engine: str
    suggested_approver: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def _parse_fact_id(rid: str) -> tuple[str, str]:
    """VF-<vendor>-<type> / AP-<approver>-<vendor>-<type> ids encode vendor and type."""
    parts = rid.split("-")
    if rid.startswith("VF-") and len(parts) >= 3:
        return parts[1], "-".join(parts[2:])
    if rid.startswith("AP-") and len(parts) >= 4:
        return parts[-2], parts[-1]
    return "", ""


class Recommender:
    def __init__(self, memory: MemoryStore, llm: Optional[GroqChooser], enrich=None):
        """enrich(record_id) -> dict of structured fields for PREC-* ids (from SQLite), used when the
        memory backend returns a recalled fact without its metadata."""
        self.memory = memory
        self.llm = llm
        self.enrich = enrich

    @property
    def engine_name(self) -> str:
        return f"groq:{self.llm.models[0]}" if self.llm else "offline"

    def _normalise(self, m: RecalledMemory) -> dict:
        fields = dict(m.fields)
        vendor, etype = m.vendor_code, m.exception_type
        if m.id.startswith("PREC-") and (not vendor or "decision" not in fields) and self.enrich:
            extra = self.enrich(m.id) or {}
            fields = {**extra, **fields}
            vendor = vendor or extra.get("vendor_code", "")
            etype = etype or extra.get("exception_type", "")
        if not vendor:
            vendor, etype2 = _parse_fact_id(m.id)
            etype = etype or etype2
        kind = m.kind if m.kind in ("decision", "vendor_fact", "approver_pref") else (
            "decision" if m.id.startswith("PREC-") else "vendor_fact" if m.id.startswith("VF-") else "approver_pref")
        return {"id": m.id, "kind": kind, "text": m.text, "score": m.score, "vendor_code": vendor,
                "exception_type": etype, "fields": fields, "source": m.source}

    def recommend(self, *, vendor_code: str, vendor_name: str, exc_type: str, magnitude: Optional[float],
                  variance_pct: Optional[float], quantity_delta: Optional[float], case: dict,
                  options: list[dict]) -> RecommendationResult:
        allowed_actions = [o["action"] for o in options]
        amount_of = {o["action"]: o["amount"] for o in options}

        def result(action, confidence, rationale, cited, recalled, engine, notes=None, approver=None):
            return RecommendationResult(action, amount_of.get(action), round(float(confidence), 2), rationale,
                                        cited, recalled, engine, approver, notes or [])

        query = RecallQuery(vendor_code=vendor_code, vendor_name=vendor_name, exception_type=exc_type,
                            variance_pct=variance_pct, quantity_delta=quantity_delta)
        try:
            raw = self.memory.recall(query, k=MAX_PRECEDENTS)[:MAX_PRECEDENTS]
        except Exception as e:
            return result("escalate", 0.0, f"Memory recall failed ({type(e).__name__}); escalating. No precedent was used.",
                          [], [], "guard")

        recalled = [self._normalise(m) for m in raw]
        for r in recalled:
            r["relevant"] = r["vendor_code"] == vendor_code and r["exception_type"] == exc_type
        relevant = [r for r in recalled if r["relevant"]]
        if not relevant:
            why = "no memories were recalled" if not recalled else (
                f"{len(recalled)} memory(ies) were recalled but none concern {vendor_name} with a "
                f"{exc_type.replace('_', ' ')} exception")
            return result("escalate", 0.0,
                          f"No relevant precedent: {why}. Escalating for a human decision; this decision will "
                          f"become the first precedent.", [], recalled, "guard")

        decisions = [r for r in relevant if r["kind"] == "decision"]
        facts = [r for r in relevant if r["kind"] != "decision"]
        relevant_ids = [r["id"] for r in relevant]
        approver = _suggested_approver(relevant)
        notes: list[str] = []

        if self.llm:
            llm_case = {
                **case,
                "allowed_options": options,
                "recalled_precedents": [
                    {"id": r["id"], "kind": r["kind"], "text": r["text"],
                     "fields": {k: v for k, v in r["fields"].items() if not k.startswith("_")}}
                    for r in relevant
                ],
            }
            choice = self.llm.choose(llm_case, relevant_ids, allowed_actions)
            if choice is None:
                return result("escalate", 0.0,
                              "The language model did not return a valid recommendation after retries "
                              "(primary and fallback). Escalating; no precedent was used.",
                              [], recalled, "guard", self.llm.last_errors[-3:])
            engine = f"groq:{self.llm.last_model}"
            action, cited, conf, rationale = choice.action, choice.cited_precedent_ids, choice.confidence, choice.rationale
            if choice.precedents_conflict and action != "escalate":
                notes.append("Model flagged conflicting precedents.")
        else:
            c = offline.choose(exc_type, magnitude, decisions, facts, allowed_actions)
            engine, action, cited, conf, rationale = "offline", c.action, c.cited, c.confidence, c.rationale

        # --- Validation (applies to both choosers) ---
        cited = [c for c in dict.fromkeys(cited) if c in relevant_ids]
        if action != "escalate" and not cited:
            return result("escalate", 0.0, "The chooser did not cite any recalled precedent, so its choice cannot be "
                          "trusted. Escalating.", [], recalled, "guard")
        if action == "approve" and magnitude is not None:
            approved = [float(r["fields"]["magnitude"]) for r in decisions
                        if r["fields"].get("decision") == "approved" and _is_num(r["fields"].get("magnitude"))]
            approved += [float(r["fields"]["approved_max"]) for r in facts if _is_num(r["fields"].get("approved_max"))]
            if approved:
                amax = max(approved)
                if magnitude > amax + offline._range_tol(exc_type, amax):
                    notes.append(f"Guard: chooser said approve, but size {magnitude:g} exceeds the largest "
                                 f"approved precedent ({amax:g}).")
                    return result("escalate", 0.3, rationale + " [Escalated by guard: larger than any approved precedent.]",
                                  cited, recalled, engine + "+guard", notes, approver)
        return result(action, conf, rationale, cited, recalled, engine, notes, approver)


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _suggested_approver(relevant: list[dict]) -> Optional[str]:
    """If every relevant decision was made by the same person (>=2 decisions), suggest them."""
    names = [(r["fields"].get("approver_name"), r["fields"].get("approver_role")) for r in relevant
             if r["kind"] == "decision" and r["fields"].get("approver_name")]
    if len(names) >= 2 and len(set(names)) == 1:
        return f"{names[0][0]} ({names[0][1]})"
    for r in relevant:
        if r["kind"] == "approver_pref" and r["fields"].get("required_approver"):
            return r["fields"]["required_approver"]
    return None
