"""Offline chooser, used when no GROQ_API_KEY is set (UI badge: "LLM: offline").

Deterministic precedent vote. It follows the same contract as the LLM: pick one allowed option,
cite only recalled IDs, escalate on conflict or when the case is outside precedent range.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..detector.options import DECISION_TO_ACTION

# How close two magnitudes must be to count as "the same kind of case", per exception type.
SIMILAR = {"price_variance": 1.5, "quantity_mismatch": 3.0, "tax_error": 1.0, "missing_po": None, "duplicate_invoice": None}
AGREE_SHARE = 0.66


@dataclass
class Choice:
    action: str
    cited: list[str]
    confidence: float
    rationale: str
    conflict: bool = False


def _mag(p: dict) -> Optional[float]:
    try:
        return float(p["fields"]["magnitude"])
    except (KeyError, TypeError, ValueError):
        return None


def _range_tol(exc_type: str, approved_max: float) -> float:
    if exc_type == "missing_po":
        return approved_max * 0.25  # amounts: 25% headroom
    return SIMILAR.get(exc_type) or 1.0


def choose(exc_type: str, magnitude: Optional[float], precedents: list[dict], facts: list[dict],
           allowed_actions: list[str]) -> Choice:
    """precedents: relevant recalled decision records; facts: relevant vendor/approver facts."""
    decisions = [p for p in precedents if p["fields"].get("decision") in DECISION_TO_ACTION]
    if not decisions:
        return Choice("escalate", [], 0.2, "Only summary facts were recalled, no individual precedent to follow; escalating.")

    def action_of(p):
        return DECISION_TO_ACTION[p["fields"]["decision"]]

    fact_ids = [f["id"] for f in facts]
    sim = SIMILAR.get(exc_type)

    # 1. Recency: among similar-sized cases, if the two most recent agree and older ones differ,
    #    the pattern has changed -> follow the recent ones.
    similar = [p for p in decisions if sim is None or magnitude is None or _mag(p) is None
               or abs(_mag(p) - magnitude) <= sim]
    similar.sort(key=lambda p: p["fields"].get("date", ""), reverse=True)
    if len(similar) >= 3:
        a0, a1 = action_of(similar[0]), action_of(similar[1])
        older = {action_of(p) for p in similar[2:]}
        if a0 == a1 and older - {a0} and a0 in allowed_actions:
            recent = similar[:2]
            changed_from = ", ".join(sorted(older - {a0}))
            return Choice(
                a0, [p["id"] for p in recent] + fact_ids, 0.7,
                f"Pattern changed: the two most recent similar cases ({recent[1]['fields'].get('date')} and "
                f"{recent[0]['fields'].get('date')}) were {recent[0]['fields']['decision']}, while earlier ones were "
                f"{changed_from}. Following the recent decisions. Latest reason: \"{recent[0]['fields'].get('reason', '')}\"",
            )

    # 2. Range guard: never recommend approving something larger than anything approved before.
    approved = [p for p in decisions if action_of(p) == "approve" and _mag(p) is not None]
    for f in facts:
        try:
            approved_max_fact = float(f["fields"]["approved_max"])
            approved.append({"id": f["id"], "fields": {"magnitude": approved_max_fact, "decision": "approved"}})
        except (KeyError, TypeError, ValueError):
            pass
    if magnitude is not None and approved:
        amax = max(_mag(p) for p in approved)
        if magnitude > amax + _range_tol(exc_type, amax):
            beyond = [p for p in decisions if action_of(p) != "approve" and _mag(p) is not None and _mag(p) >= amax]
            acts = {action_of(p) for p in beyond}
            if len(acts) == 1 and (a := acts.pop()) in allowed_actions and a != "escalate":
                return Choice(
                    a, [p["id"] for p in beyond] + fact_ids, 0.6,
                    f"Current size {magnitude:g} is above the largest approved precedent ({amax:g}); similar larger "
                    f"cases were {beyond[0]['fields']['decision']} (\"{beyond[0]['fields'].get('reason', '')}\").",
                )
            return Choice(
                "escalate", [p["id"] for p in approved if p["id"].startswith("PREC-")][:3] + fact_ids, 0.3,
                f"Current size {magnitude:g} exceeds the largest approved precedent ({amax:g}) and no precedent "
                f"covers a case this large. Escalating.",
            )

    # 3. Similarity-weighted vote.
    weights: dict[str, float] = {}
    for p in decisions:
        w = 1.0
        if magnitude is not None and _mag(p) is not None:
            scale = sim or max(1.0, magnitude * 0.25)
            w = 1.0 / (1.0 + abs(_mag(p) - magnitude) / scale)
        weights[action_of(p)] = weights.get(action_of(p), 0.0) + w
    total = sum(weights.values())
    best, bw = max(weights.items(), key=lambda kv: kv[1])
    share = bw / total
    if share < AGREE_SHARE or best not in allowed_actions:
        parts = ", ".join(f"{k}: {v / total:.0%}" for k, v in sorted(weights.items(), key=lambda kv: -kv[1]))
        return Choice("escalate", [p["id"] for p in decisions] + fact_ids, 0.3,
                      f"Precedents conflict ({parts}). A senior should decide.", conflict=True)
    backing = [p for p in decisions if action_of(p) == best]
    conf = round(min(0.95, share * min(1.0, 0.5 + 0.2 * len(backing))), 2)
    lead = backing[0]["fields"]
    return Choice(
        best, [p["id"] for p in backing] + fact_ids, conf,
        f"{len(backing)} of {len(decisions)} recalled precedent(s) for this vendor and exception type were "
        f"{lead['decision']}. Most relevant reason: \"{lead.get('reason', '')}\" ({lead.get('approver_name', '?')}, "
        f"{lead.get('date', '?')}).",
    )
