"""Deterministic synthetic AP data: 10 vendors, 3 batches x 30 invoices, POs and goods receipts.

Run:  python -m backend.data.seed        (recreates the SQLite database)

Planted vendor quirks (the agent never reads these notes; it must learn from decisions):
  V01 Northwind Fuel & Freight   adds a fuel-surcharge line, 4-7% over PO (seniors: approve up to 8%)
  V02 Halden Precision Parts     small price variances; only Priya Raman approves them
  V03 Crestline Office Supply    sends duplicate invoices
  V04 Bluegate Packaging         short deliveries (invoices ordered qty, receives less)
  V05 Meridian Chemicals         charges 18% GST instead of the contracted 12%
  V06 Arbor Facilities           3% escalator approved in June; clause lapsed -> rejected from July (quirk changed)
  V07 Solano Logistics           peak surcharge approved once, rejected once (conflicting precedents)
  V08 Keystone IT Solutions      monthly support billed without PO under a blanket agreement; one big no-PO bill
  V09 Pinecrest Catering         small menu price drift ~1%
  V10 Vantage Lab Instruments    first appears in batch 3 with a variance (no history)
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..db import Base, Batch, GoodsReceipt, Invoice, PurchaseOrder, Vendor, engine as default_engine
from ..detector.match import money

SEED = 20260601

VENDORS = [
    # code, name, terms, currency, tax label, tax rate, quirk note
    ("V01", "Northwind Fuel & Freight Inc.", "Net 30", "USD", "Sales tax", "0.075", "Fuel surcharge line 4-7% over PO"),
    ("V02", "Halden Precision Parts GmbH", "Net 45", "EUR", "VAT", "0.19", "Variances approved only by Priya Raman"),
    ("V03", "Crestline Office Supply Co.", "Net 30", "USD", "Sales tax", "0.075", "Sends duplicate invoices"),
    ("V04", "Bluegate Packaging Ltd", "Net 60", "GBP", "VAT", "0.20", "Short deliveries"),
    ("V05", "Meridian Chemicals Pvt Ltd", "Net 30", "INR", "GST", "0.12", "Bills 18% GST instead of 12%"),
    ("V06", "Arbor Facilities Services LLC", "Net 30", "USD", "Sales tax", "0", "3% escalator; lapsed 30 June"),
    ("V07", "Solano Logistics S.A.", "Net 30", "EUR", "VAT", "0.21", "Peak surcharge: conflicting decisions"),
    ("V08", "Keystone IT Solutions", "Net 15", "USD", "Sales tax", "0", "No-PO monthly support (blanket agreement)"),
    ("V09", "Pinecrest Catering Co.", "Net 15", "USD", "Sales tax", "0.075", "Menu price drift ~1%"),
    ("V10", "Vantage Lab Instruments Ltd", "Net 30", "GBP", "VAT", "0.20", "New vendor in August"),
]

# sku, description, unit price, (min qty, max qty, step)
CATALOG = {
    "V01": [("FRT-LTL", "LTL freight, Chicago-Dallas lane (per load)", "1450.00", (1, 6, 1)),
            ("FRT-FTL", "Full truckload, Chicago-Atlanta (per load)", "2980.00", (1, 4, 1)),
            ("FRT-DRAY", "Port drayage, Long Beach (per container)", "640.00", (2, 10, 1))],
    "V02": [("HPP-BRG-6204", "Deep groove ball bearing 6204-2RS", "4.85", (200, 1200, 100)),
            ("HPP-SHF-20", "Hardened shaft 20mm x 500mm", "38.40", (20, 120, 10)),
            ("HPP-CPL-12", "Jaw coupling 12mm", "22.10", (20, 200, 20))],
    "V03": [("CRS-PPR-A4", "Copy paper A4 80gsm (box of 5 reams)", "27.50", (10, 60, 5)),
            ("CRS-TNR-58A", "Toner cartridge 58A", "89.00", (2, 12, 1)),
            ("CRS-CHR-ERG", "Ergonomic task chair", "214.00", (1, 8, 1))],
    "V04": [("BGP-BOX-L", "Double-wall carton 600x400x400", "1.18", (500, 3000, 250)),
            ("BGP-TAPE-48", "Packing tape 48mm x 66m", "0.92", (200, 1200, 100)),
            ("BGP-WRAP-500", "Pallet stretch wrap 500mm", "11.40", (20, 120, 10))],
    "V05": [("MCL-IPA-20", "Isopropyl alcohol 99% (20 L drum)", "2150.00", (4, 30, 2)),
            ("MCL-NAOH-25", "Caustic soda flakes (25 kg bag)", "1480.00", (10, 60, 5)),
            ("MCL-CIT-25", "Citric acid anhydrous (25 kg bag)", "3120.00", (4, 20, 2))],
    "V06": [("ARB-JAN-M", "Monthly janitorial service, HQ campus", "6400.00", (1, 1, 1)),
            ("ARB-HVAC-Q", "HVAC preventive maintenance visit", "1250.00", (1, 3, 1))],
    "V07": [("SOL-CNT-40", "40ft container, Valencia-Rotterdam", "2350.00", (1, 6, 1)),
            ("SOL-CUS", "Customs brokerage (per entry)", "180.00", (1, 6, 1))],
    "V08": [("KIT-M365", "Microsoft 365 E3 licence (monthly)", "36.00", (50, 250, 10)),
            ("KIT-LAP", "Laptop imaging and setup", "95.00", (5, 30, 5))],
    "V09": [("PCC-LUNCH", "Staff lunch buffet (per head)", "14.50", (40, 160, 10)),
            ("PCC-COFFEE", "Coffee service (per day)", "85.00", (5, 22, 1))],
    "V10": [("VLI-PIP-1000", "Adjustable pipette 100-1000 uL", "214.00", (2, 10, 1)),
            ("VLI-TIPS", "Filter tips 1000 uL (rack of 96)", "9.80", (20, 100, 10))],
}
NO_PO_ITEMS = {
    2400: ("KIT-MSP", "Managed endpoint support - monthly fee", "2400.00"),
    18500: ("KIT-FW", "Firewall appliance refresh project (hardware + install)", "18500.00"),
}

# Planted cases per batch: (invoice number, vendor, kind, param). kind: price | qty | tax | dup | missing_po | clean
PLAN = {
    1: [
        ("NFF-24101", "V01", "price", "5.1"), ("NFF-24107", "V01", "price", "4.4"), ("NFF-24115", "V01", "price", "6.2"),
        ("HPP-88012", "V02", "price", "2.1"), ("HPP-88019", "V02", "price", "1.6"),
        ("CRS-50311", "V03", "clean", None), ("CRS-50318", "V03", "clean", None),
        ("BGP-7702", "V04", "qty", "50"), ("BGP-7709", "V04", "qty", "30"),
        ("MCL-3301", "V05", "tax", "0.18"), ("MCL-3308", "V05", "tax", "0.18"),
        ("ARB-1106", "V06", "price", "3.0"), ("ARB-1107", "V06", "price", "3.0"),
        ("SOL-4420", "V07", "price", "6.0"),
        ("KIT-9051", "V08", "missing_po", "2400"),
        ("PCC-6101", "V09", "price", "1.2"), ("PCC-6104", "V09", "price", "0.9"),
        ("CRS-50311", "V03", "dup", "CRS-50311"), ("CRS-50318A", "V03", "dup", "CRS-50318"),
    ],
    2: [
        ("NFF-24130", "V01", "price", "5.6"), ("NFF-24136", "V01", "price", "4.8"),
        ("NFF-24141", "V01", "price", "9.5"), ("NFF-24149", "V01", "price", "6.9"),
        ("HPP-88031", "V02", "price", "1.9"), ("HPP-88037", "V02", "price", "2.4"),
        ("CRS-50340", "V03", "clean", None),
        ("BGP-7731", "V04", "qty", "40"), ("BGP-7738", "V04", "qty", "25"),
        ("MCL-3322", "V05", "tax", "0.18"),
        ("ARB-1112", "V06", "price", "3.0"), ("ARB-1113", "V06", "price", "3.0"),
        ("SOL-4436", "V07", "price", "5.8"),
        ("KIT-9064", "V08", "missing_po", "2400"),
        ("PCC-6120", "V09", "price", "1.1"),
        ("CRS-50340", "V03", "dup", "CRS-50340"),
    ],
    3: [
        ("NFF-24160", "V01", "price", "5.3"), ("NFF-24166", "V01", "price", "6.4"), ("NFF-24172", "V01", "price", "10.2"),
        ("HPP-88050", "V02", "price", "2.0"),
        ("CRS-50371", "V03", "clean", None), ("CRS-50377", "V03", "clean", None),
        ("BGP-7760", "V04", "qty", "45"), ("BGP-7766", "V04", "qty", "60"),
        ("MCL-3340", "V05", "tax", "0.18"), ("MCL-3346", "V05", "tax", "0.18"),
        ("ARB-1120", "V06", "price", "3.0"),
        ("SOL-4451", "V07", "price", "6.1"),
        ("KIT-9077", "V08", "missing_po", "2400"), ("KIT-9080", "V08", "missing_po", "18500"),
        ("PCC-6133", "V09", "price", "1.3"),
        ("VLI-0012", "V10", "price", "3.2"),
        ("CRS-50371", "V03", "dup", "CRS-50371"), ("CRS-50377-1", "V03", "dup", "CRS-50377"),
    ],
}
BATCHES = {1: ("Batch 1 - June 2026", "2026-06", date(2026, 6, 1)),
           2: ("Batch 2 - July 2026", "2026-07", date(2026, 7, 1)),
           3: ("Batch 3 - August 2026", "2026-08", date(2026, 8, 1))}
INVOICES_PER_BATCH = 30
PREFIX = {"V01": "NFF", "V02": "HPP", "V03": "CRS", "V04": "BGP", "V05": "MCL", "V06": "ARB", "V07": "SOL",
          "V08": "KIT", "V09": "PCC", "V10": "VLI"}


class _Gen:
    def __init__(self, db: Session):
        self.db = db
        self.rng = random.Random(SEED)
        self.po_seq = 4100
        self.gr_seq = 7100
        self.inv_seq = {k: 30000 for k in PREFIX}
        self.by_number: dict[str, Invoice] = {}
        self.tax = {v[0]: Decimal(v[5]) for v in VENDORS}
        self.ccy = {v[0]: v[3] for v in VENDORS}

    def _qty(self, rng_spec) -> int:
        lo, hi, step = rng_spec
        return self.rng.randrange(lo, hi + 1, step)

    def _po_lines(self, vendor: str) -> list[dict]:
        cat = CATALOG[vendor]
        k = 1 if len(cat) == 1 else self.rng.choice([1, 2, 2, 3][: len(cat) + 1])
        items = sorted(self.rng.sample(cat, min(k, len(cat))), key=lambda c: c[0])
        return [{"sku": s, "description": d, "qty": self._qty(q), "unit_price": p} for s, d, p, q in items]

    def _add_invoice(self, batch_id, number, vendor, day, po, lines, tax_rate) -> Invoice:
        sub = sum((money(Decimal(str(l["qty"])) * Decimal(l["unit_price"])) for l in lines), Decimal("0"))
        tax = money(sub * tax_rate)
        inv = Invoice(invoice_number=number, vendor_code=vendor, po_number=po, batch_id=batch_id,
                      invoice_date=day.isoformat(), currency=self.ccy[vendor], lines=lines,
                      subtotal=sub, tax_amount=tax, total=sub + tax, status="new")
        self.db.add(inv)
        self.db.flush()
        return inv

    def _po_and_receipt(self, vendor, day, lines, short: int = 0) -> str:
        self.po_seq += 1
        po_no = f"PO-2026-{self.po_seq:05d}"
        self.db.add(PurchaseOrder(po_number=po_no, vendor_code=vendor, currency=self.ccy[vendor],
                                  order_date=(day - timedelta(days=self.rng.randint(10, 25))).isoformat(),
                                  lines=lines))
        self.db.flush()
        self.gr_seq += 1
        rec = [{"sku": l["sku"], "qty_received": l["qty"]} for l in lines]
        if short:
            rec[0]["qty_received"] = lines[0]["qty"] - short
        self.db.add(GoodsReceipt(gr_number=f"GR-26-{self.gr_seq:05d}", po_number=po_no,
                                 received_date=(day - timedelta(days=self.rng.randint(1, 7))).isoformat(),
                                 lines=rec))
        return po_no

    def planted(self, batch_id: int, number: str, vendor: str, kind: str, param, day: date) -> None:
        tax_rate = self.tax[vendor]
        if kind == "missing_po":
            sku, desc, price = NO_PO_ITEMS[int(param)]
            self._add_invoice(batch_id, number, vendor, day, None,
                              [{"sku": sku, "description": desc, "qty": 1, "unit_price": price}], tax_rate)
            return
        if kind == "dup":
            orig = self.by_number[param]
            self._add_invoice(batch_id, number, vendor, day, orig.po_number, [dict(l) for l in orig.lines], tax_rate)
            return
        lines = self._po_lines(vendor)
        if kind == "qty":
            short = int(param)
            if lines[0]["qty"] <= short * 2:
                lines[0]["qty"] = short * 20
        po_no = self._po_and_receipt(vendor, day, lines, short=int(param) if kind == "qty" else 0)
        inv_lines = [dict(l) for l in lines]
        if kind == "price":
            pctv = Decimal(param) / 100
            if vendor == "V01":  # fuel surcharge as an extra line not on the PO
                base = sum((money(Decimal(str(l["qty"])) * Decimal(l["unit_price"])) for l in lines), Decimal("0"))
                inv_lines.append({"sku": "FSC", "description": "Fuel surcharge", "qty": 1,
                                  "unit_price": str(money(base * pctv))})
            else:
                for l in inv_lines:
                    l["unit_price"] = str(money(Decimal(l["unit_price"]) * (1 + pctv)))
        if kind == "tax":
            tax_rate = Decimal(param)
        inv = self._add_invoice(batch_id, number, vendor, day, po_no, inv_lines, tax_rate)
        self.by_number[number] = inv

    def clean(self, batch_id: int, vendor: str, day: date) -> None:
        self.inv_seq[vendor] += self.rng.randint(1, 9)
        number = f"{PREFIX[vendor]}-{self.inv_seq[vendor]}"
        lines = self._po_lines(vendor)
        po_no = self._po_and_receipt(vendor, day, lines)
        self._add_invoice(batch_id, number, vendor, day, po_no, [dict(l) for l in lines], self.tax[vendor])


def seed(eng=None) -> dict:
    eng = eng or default_engine
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as db:
        for code, name, terms, ccy, label, rate, note in VENDORS:
            db.add(Vendor(code=code, name=name, payment_terms=terms, currency=ccy, tax_label=label,
                          tax_rate=Decimal(rate), notes=note))
        g = _Gen(db)
        for b, (name, period, start) in BATCHES.items():
            db.add(Batch(id=b, name=name, period=period, status="new"))
            db.flush()
            plan = PLAN[b]
            n_clean = INVOICES_PER_BATCH - len(plan)
            clean_vendors = ["V01", "V02", "V03", "V04", "V05", "V06", "V07", "V08", "V09"] + (["V10"] if b == 3 else [])
            # Interleave clean invoices first-half/second-half so originals precede their duplicates.
            days = sorted(g.rng.randint(0, 27) for _ in range(INVOICES_PER_BATCH))
            entries = [("plan", p) for p in plan if p[2] != "dup"]
            entries += [("clean", clean_vendors[i % len(clean_vendors)]) for i in range(n_clean)]
            g.rng.shuffle(entries)
            entries += [("plan", p) for p in plan if p[2] == "dup"]  # duplicates arrive last
            for (kind, item), d in zip(entries, days):
                day = start + timedelta(days=d)
                if kind == "plan" and item[2] == "dup":  # resubmitted a few days after the original
                    day = date.fromisoformat(g.by_number[item[3]].invoice_date) + timedelta(days=g.rng.randint(2, 6))
                if kind == "plan":
                    g.planted(b, item[0], item[1], item[2], item[3], day)
                else:
                    g.clean(b, item, day)
        db.commit()
        counts = {"vendors": db.query(Vendor).count(), "invoices": db.query(Invoice).count(),
                  "purchase_orders": db.query(PurchaseOrder).count(), "goods_receipts": db.query(GoodsReceipt).count()}
    return counts


if __name__ == "__main__":
    print(seed())
