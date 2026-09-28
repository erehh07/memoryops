"""Deterministic three-way match: invoice vs purchase order vs goods receipt.

All arithmetic uses Decimal with ROUND_HALF_UP. No LLM involved.

Rules (in order):
  missing_po         invoice has no PO number, or the PO does not exist (other checks skipped)
  duplicate_invoice  an earlier invoice from the same vendor has the same invoice number, or the
                     same PO + same total within 14 days (other checks skipped)
  price_variance     (invoice subtotal - invoiced qty at PO prices) / (invoiced qty at PO prices),
                     reported as a percent; exception when |variance| > tolerance (strictly).
                     Lines not on the PO (e.g. surcharges) count toward the invoice subtotal.
  quantity_mismatch  sum over PO lines of (invoiced qty - received qty) != 0
  tax_error          |invoice tax - round(subtotal x vendor rate, 2)| > 0.01
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

CENT = Decimal("0.01")
PCT = Decimal("0.01")
TAX_TOLERANCE = Decimal("0.01")
DUPLICATE_WINDOW_DAYS = 14


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def pct(x: Decimal) -> Decimal:
    return x.quantize(PCT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    sku: str
    qty: Decimal
    unit_price: Decimal = Decimal("0")
    description: str = ""

    @staticmethod
    def of(d: dict) -> "Line":
        return Line(
            sku=d["sku"],
            qty=Decimal(str(d.get("qty", d.get("qty_received", 0)))),
            unit_price=Decimal(str(d.get("unit_price", 0))),
            description=d.get("description", ""),
        )


@dataclass
class InvoiceDoc:
    id: int
    invoice_number: str
    vendor_code: str
    po_number: Optional[str]
    invoice_date: str
    lines: list[Line]
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal


@dataclass
class PODoc:
    po_number: str
    vendor_code: str
    lines: list[Line]


@dataclass
class ReceiptDoc:
    po_number: str
    lines: list[Line]  # qty = received


@dataclass
class DetectedException:
    type: str
    variance_pct: Optional[Decimal] = None
    quantity_delta: Optional[Decimal] = None
    magnitude: Optional[Decimal] = None  # comparable size used for precedent matching
    details: dict = field(default_factory=dict)


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}


def detect(
    inv: InvoiceDoc,
    po: Optional[PODoc],
    receipts: list[ReceiptDoc],
    tax_rate: Decimal,
    earlier_invoices: list[InvoiceDoc],
    price_tolerance_pct: Decimal = Decimal("0.5"),
) -> list[DetectedException]:
    # 1. missing PO
    if not inv.po_number or po is None:
        return [
            DetectedException(
                type="missing_po",
                magnitude=money(inv.total),
                details=_jsonable({
                    "po_number_on_invoice": inv.po_number,
                    "reason": "no PO number on invoice" if not inv.po_number else "PO not found",
                    "invoice_total": money(inv.total),
                }),
            )
        ]

    # 2. duplicate
    dup = find_duplicate(inv, earlier_invoices)
    if dup is not None:
        return [
            DetectedException(
                type="duplicate_invoice",
                magnitude=money(inv.total),
                details=_jsonable({
                    "duplicate_of_invoice_id": dup[0].id,
                    "duplicate_of_number": dup[0].invoice_number,
                    "match_rule": dup[1],
                    "invoice_total": money(inv.total),
                }),
            )
        ]

    out: list[DetectedException] = []
    po_price = {l.sku: l.unit_price for l in po.lines}

    # 3. price variance
    inv_sub = sum((money(l.qty * l.unit_price) for l in inv.lines), Decimal("0"))
    expected = sum((money(l.qty * po_price[l.sku]) for l in inv.lines if l.sku in po_price), Decimal("0"))
    off_po = [l.sku for l in inv.lines if l.sku not in po_price]
    if expected > 0:
        variance = pct((inv_sub - expected) / expected * 100)
        if abs(variance) > price_tolerance_pct:
            out.append(
                DetectedException(
                    type="price_variance",
                    variance_pct=variance,
                    magnitude=abs(variance),
                    details=_jsonable({
                        "invoice_subtotal": inv_sub,
                        "expected_subtotal_at_po_prices": expected,
                        "difference": inv_sub - expected,
                        "lines_not_on_po": off_po,
                        "tolerance_pct": price_tolerance_pct,
                    }),
                )
            )

    # 4. quantity mismatch (invoiced vs received, PO lines only)
    received: dict[str, Decimal] = {}
    for r in receipts:
        for l in r.lines:
            received[l.sku] = received.get(l.sku, Decimal("0")) + l.qty
    per_line = []
    delta_total = Decimal("0")
    received_total = Decimal("0")
    for l in inv.lines:
        if l.sku not in po_price:
            continue
        rq = received.get(l.sku, Decimal("0"))
        d = l.qty - rq
        received_total += rq
        if d != 0:
            per_line.append({"sku": l.sku, "invoiced": str(l.qty), "received": str(rq), "delta": str(d)})
        delta_total += d
    if per_line:
        short_pct = pct(abs(delta_total) / received_total * 100) if received_total else Decimal("100.00")
        out.append(
            DetectedException(
                type="quantity_mismatch",
                quantity_delta=delta_total,
                magnitude=short_pct,
                details={"lines": per_line, "delta_pct_of_received": str(short_pct)},
            )
        )

    # 5. tax
    expected_tax = money(money(inv.subtotal) * tax_rate)
    tax_diff = money(inv.tax_amount) - expected_tax
    if abs(tax_diff) > TAX_TOLERANCE:
        implied = pct(money(inv.tax_amount) / money(inv.subtotal) * 100) if inv.subtotal else Decimal("0")
        out.append(
            DetectedException(
                type="tax_error",
                magnitude=abs(pct(implied - tax_rate * 100)),
                details=_jsonable({
                    "expected_rate_pct": pct(tax_rate * 100),
                    "implied_rate_pct": implied,
                    "expected_tax": expected_tax,
                    "invoice_tax": money(inv.tax_amount),
                    "difference": tax_diff,
                }),
            )
        )
    return out


def find_duplicate(inv: InvoiceDoc, earlier: list[InvoiceDoc]) -> Optional[tuple[InvoiceDoc, str]]:
    for e in earlier:
        if e.id == inv.id or e.vendor_code != inv.vendor_code:
            continue
        if e.invoice_number.strip().upper() == inv.invoice_number.strip().upper():
            return e, "same vendor invoice number"
    d_inv = date.fromisoformat(inv.invoice_date)
    for e in earlier:
        if e.id == inv.id or e.vendor_code != inv.vendor_code:
            continue
        if (
            e.po_number
            and e.po_number == inv.po_number
            and money(e.total) == money(inv.total)
            and abs((date.fromisoformat(e.invoice_date) - d_inv).days) <= DUPLICATE_WINDOW_DAYS
        ):
            return e, "same PO and total within 14 days"
    return None
