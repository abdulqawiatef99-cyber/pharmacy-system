"""SYNTHETIC test invoices. These are NOT real supplier files: they imitate the
common layout of Egyptian distributor sheets (header block, item table,
totals row) so the importer logic can be tested. Real samples are still needed
to tune per-supplier differences (see docs/PROGRESS.md)."""
from __future__ import annotations

import io

from openpyxl import Workbook


def excel_invoice(lines: list[tuple], *, invoice_no="INV-1001", supplier="ابن سينا فارما",
                  total=None, arabic=True, date="20/10/2026") -> bytes:
    """lines: (name, qty, bonus, public, disc_pct, net, total, batch, expiry, barcode)"""
    wb = Workbook()
    ws = wb.active
    ws.append(["شركة " + supplier + " لتوزيع الأدوية"])
    ws.append(["رقم الفاتورة", invoice_no, None, "التاريخ", date])
    ws.append(["المورد", supplier])
    ws.append([])
    if arabic:
        ws.append(["م", "الباركود", "الصنف", "الكمية", "بونص", "سعر الجمهور", "الخصم %",
                   "سعر الصيدلي", "الإجمالي", "التشغيلة", "الصلاحية"])
    else:
        ws.append(["#", "Barcode", "Item Name", "Qty", "Bonus", "Public Price", "Disc %",
                   "Net Price", "Total", "Batch", "Exp"])
    computed = 0
    for i, (name, qty, bonus, pub, disc, net, tot, batch, exp, bc) in enumerate(lines, 1):
        ws.append([i, bc, name, qty, bonus, pub, disc, net, tot, batch, exp])
        computed += tot or 0
    ws.append([])
    ws.append([None, None, "الإجمالي", None, None, None, None, None,
               total if total is not None else round(computed, 2)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_invoice_fraction_discounts() -> bytes:
    rows = [
        "Invoice No: C-77",
        "Item,Qty,Bonus,Public Price,Discount,Net Price,Total,Batch,Expiry",
        "Panadol 500mg 24 tab,10,0,40.00,0.20,32.00,320.00,P1,03/2028",
        "Cataflam 50mg 20 tab,5,1,60.00,0.18,49.20,246.00,C9,2027-11",
        "Total,,,,,,566.00,,",
    ]
    return ("\n".join(rows) + "\n").encode("utf-8")
