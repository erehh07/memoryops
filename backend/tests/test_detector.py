from decimal import Decimal as D

import pytest

from backend.detector.match import InvoiceDoc, Line, PODoc, ReceiptDoc, detect, money
from backend.detector.options import build_options

RATE = D("0.10")


def inv(lines, *, id=2, number="INV-1", po="PO-1", date="2026-06-10", tax=None, vendor="V01"):
    sub = sum((money(D(str(q)) * D(str(p))) for _, q, p in lines), D("0"))
    t = money(sub * RATE) if tax is None else D(str(tax))
    return InvoiceDoc(id, number, vendor, po, date, [Line(s, D(str(q)), D(str(p))) for s, q, p in lines], sub, t, sub + t)


def po(lines):
    return PODoc("PO-1", "V01", [Line(s, D(str(q)), D(str(p))) for s, q, p in lines])


def gr(lines):
    return [ReceiptDoc("PO-1", [Line(s, D(str(q))) for s, q in lines])]


BASE_PO = [("A", 10, "100.00"), ("B", 5, "20.00")]  # expected subtotal 1100.00


def run(i, p=None, r=None, earlier=(), tol=D("0.5")):
    p = po(BASE_PO) if p is None else p
    r = gr([("A", 10), ("B", 5)]) if r is None else r
    return detect(i, p, r, RATE, list(earlier), tol)


def types(found):
    return [f.type for f in found]


def test_clean_match_has_no_exceptions():
    assert run(inv([("A", 10, "100.00"), ("B", 5, "20.00")])) == []


# ---------------- price variance
def test_price_variance_percent_and_sign():
    found = run(inv([("A", 10, "105.00"), ("B", 5, "20.00")]))  # +50 on 1100
    assert types(found) == ["price_variance"]
    assert found[0].variance_pct == D("4.55")  # 4.5454.. rounds half up
    assert found[0].magnitude == D("4.55")


def test_negative_price_variance():
    found = run(inv([("A", 10, "95.00"), ("B", 5, "20.00")]))
    assert found[0].variance_pct == D("-4.55")
    assert found[0].magnitude == D("4.55")


def test_price_variance_boundary_exactly_at_tolerance_is_not_exception():
    # 1100 * 0.5% = 5.50 over -> exactly 0.50%, not strictly above tolerance
    assert run(inv([("A", 10, "100.00"), ("B", 5, "21.10")])) == []


def test_price_variance_just_above_tolerance_is_exception():
    found = run(inv([("A", 10, "100.00"), ("B", 5, "21.12")]))  # +5.60 -> 0.509% -> 0.51
    assert types(found) == ["price_variance"]
    assert found[0].variance_pct == D("0.51")


def test_price_variance_rounding_half_up():
    # +0.55/100 = 0.55% exactly on base 100
    i = inv([("A", 1, "100.55")])
    found = run(i, p=po([("A", 1, "100.00")]), r=gr([("A", 1)]))
    assert found[0].variance_pct == D("0.55")
    # line totals are rounded to cents first: 3 x 100.3333 = 300.9999 -> 301.00, so 1/3 % -> 0.33
    found = run(inv([("A", 3, "100.3333")]), p=po([("A", 3, "100.00")]), r=gr([("A", 3)]), tol=D("0"))
    assert found[0].variance_pct == D("0.33")
    # 2/3 % -> 0.67 (half-up at the second decimal)
    found = run(inv([("A", 3, "100.6667")]), p=po([("A", 3, "100.00")]), r=gr([("A", 3)]), tol=D("0"))
    assert found[0].variance_pct == D("0.67")


def test_surcharge_line_not_on_po_counts_as_price_variance():
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00"), ("FSC", 1, "55.00")]))
    assert types(found) == ["price_variance"]
    assert found[0].variance_pct == D("5.00")
    assert found[0].details["lines_not_on_po"] == ["FSC"]


# ---------------- quantity mismatch
def test_quantity_short_delivery():
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00")]), r=gr([("A", 8), ("B", 5)]))
    assert types(found) == ["quantity_mismatch"]
    assert found[0].quantity_delta == D("2")
    assert found[0].magnitude == D("15.38")  # 2 / 13 received


def test_quantity_over_receipt_is_negative_delta():
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00")]), r=gr([("A", 11), ("B", 5)]))
    assert found[0].quantity_delta == D("-1")


def test_quantity_nothing_received():
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00")]), r=[])
    assert found[0].type == "quantity_mismatch"
    assert found[0].magnitude == D("100.00")


def test_multiple_receipts_are_summed():
    r = [ReceiptDoc("PO-1", [Line("A", D(6)), Line("B", D(5))]), ReceiptDoc("PO-1", [Line("A", D(4))])]
    assert run(inv([("A", 10, "100.00"), ("B", 5, "20.00")]), r=r) == []


# ---------------- tax
def test_tax_error_wrong_rate():
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00")], tax="198.00"))
    assert types(found) == ["tax_error"]
    d = found[0].details
    assert d["expected_tax"] == "110.00" and d["implied_rate_pct"] == "18.00"
    assert found[0].magnitude == D("8.00")


def test_tax_one_cent_off_is_tolerated_two_cents_is_not():
    assert run(inv([("A", 10, "100.00"), ("B", 5, "20.00")], tax="110.01")) == []
    assert types(run(inv([("A", 10, "100.00"), ("B", 5, "20.00")], tax="110.02"))) == ["tax_error"]


def test_tax_rounding_half_up():
    # subtotal 0.05 * 10% = 0.005 -> 0.01 half-up
    i = inv([("A", 1, "0.05")], tax="0.01")
    assert run(i, p=po([("A", 1, "0.05")]), r=gr([("A", 1)])) == []


# ---------------- duplicate
def test_duplicate_same_number():
    first = inv([("A", 10, "100.00"), ("B", 5, "20.00")], id=1)
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00")], id=2, number="inv-1 "), earlier=[first])
    assert types(found) == ["duplicate_invoice"]
    assert found[0].details["match_rule"] == "same vendor invoice number"


def test_duplicate_same_po_and_total_within_window():
    first = inv([("A", 10, "100.00"), ("B", 5, "20.00")], id=1, number="X-1", date="2026-06-01")
    found = run(inv([("A", 10, "100.00"), ("B", 5, "20.00")], number="X-1A", date="2026-06-15"), earlier=[first])
    assert types(found) == ["duplicate_invoice"]


def test_not_duplicate_outside_window_or_other_vendor():
    first = inv([("A", 10, "100.00"), ("B", 5, "20.00")], id=1, number="X-1", date="2026-06-01")
    assert run(inv([("A", 10, "100.00"), ("B", 5, "20.00")], number="X-2", date="2026-06-16"), earlier=[first]) == []
    other = inv([("A", 10, "100.00"), ("B", 5, "20.00")], id=1, vendor="V02")
    assert run(inv([("A", 10, "100.00"), ("B", 5, "20.00")]), earlier=[other]) == []


# ---------------- missing PO
def test_missing_po_number():
    found = detect(inv([("X", 1, "2400.00")], po=None), None, [], RATE, [])
    assert types(found) == ["missing_po"]
    assert found[0].magnitude == D("2640.00")


def test_unknown_po_number():
    found = detect(inv([("X", 1, "10.00")], po="PO-NOPE"), None, [], RATE, [])
    assert found[0].details["reason"] == "PO not found"


# ---------------- combined + options
def test_price_and_quantity_together():
    found = run(inv([("A", 10, "110.00"), ("B", 5, "20.00")]), r=gr([("A", 9), ("B", 5)]))
    assert sorted(types(found)) == ["price_variance", "quantity_mismatch"]


@pytest.mark.parametrize("etype", ["price_variance", "quantity_mismatch", "tax_error", "duplicate_invoice", "missing_po"])
def test_options_always_include_escalate(etype):
    details = {"expected_subtotal_at_po_prices": "1100.00"}
    invoice = {"total": "1265.00", "subtotal": "1150.00", "lines": [{"sku": "A", "qty": 10, "unit_price": "105.00"},
                                                                    {"sku": "B", "qty": 5, "unit_price": "20.00"}]}
    opts = build_options(etype, details, invoice, [{"sku": "A"}, {"sku": "B"}], {"A": D(9), "B": D(5)}, RATE)
    acts = [o["action"] for o in opts]
    assert acts[-1] == "escalate"
    if etype == "price_variance":
        assert dict((o["action"], o["amount"]) for o in opts)["adjust"] == "1210.00"
    if etype == "quantity_mismatch":
        assert dict((o["action"], o["amount"]) for o in opts)["adjust"] == "1149.50"  # 9*105+100=1045 +10%
    if etype == "tax_error":
        assert dict((o["action"], o["amount"]) for o in opts)["adjust"] == "1265.00"
