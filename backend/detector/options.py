"""Deterministic resolution options + amounts for an exception. The LLM only picks one of these."""
from __future__ import annotations

from decimal import Decimal

from .match import money

ACTION_TO_DECISION = {"approve": "approved", "reject": "rejected", "adjust": "adjusted", "escalate": "escalated"}
DECISION_TO_ACTION = {v: k for k, v in ACTION_TO_DECISION.items()}


def build_options(exc_type: str, details: dict, invoice: dict, po_lines: list[dict] | None,
                  received: dict[str, Decimal], tax_rate: Decimal) -> list[dict]:
    """Returns [{action, label, amount}] — amount is what would be approved for payment (never paid)."""
    total = money(invoice["total"])
    subtotal = money(invoice["subtotal"])
    opts: list[dict] = []

    def add(action: str, label: str, amount):
        opts.append({"action": action, "label": label, "amount": None if amount is None else str(money(amount))})

    if exc_type == "price_variance":
        exp = money(details["expected_subtotal_at_po_prices"])
        add("approve", "Approve as billed", total)
        add("adjust", "Short-pay to PO prices (request credit note for the difference)", exp + money(exp * tax_rate))
        add("reject", "Reject invoice and return to vendor", Decimal("0"))
    elif exc_type == "quantity_mismatch":
        price = {l["sku"]: Decimal(str(l["unit_price"])) for l in invoice["lines"]}
        po_skus = {l["sku"] for l in (po_lines or [])}
        sub = Decimal("0")
        for l in invoice["lines"]:
            q = Decimal(str(l["qty"]))
            if l["sku"] in po_skus:
                q = min(q, received.get(l["sku"], Decimal("0")))
            sub += money(q * price[l["sku"]])
        add("approve", "Approve as billed", total)
        add("adjust", "Pay for received quantity only", sub + money(sub * tax_rate))
        add("reject", "Reject invoice and return to vendor", Decimal("0"))
    elif exc_type == "tax_error":
        add("approve", "Approve as billed", total)
        add("adjust", "Pay with tax recalculated at the contracted rate", subtotal + money(subtotal * tax_rate))
        add("reject", "Reject and request a corrected invoice", Decimal("0"))
    elif exc_type == "duplicate_invoice":
        add("reject", "Reject as duplicate", Decimal("0"))
        add("approve", "Approve (treat as a separate, legitimate invoice)", total)
    elif exc_type == "missing_po":
        add("approve", "Approve without PO (e.g. under a blanket agreement)", total)
        add("reject", "Reject and ask vendor/requester for a PO", Decimal("0"))
    add("escalate", "Escalate to a senior approver", None)
    return opts
