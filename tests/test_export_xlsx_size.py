"""A screen's table exported to Excel is not cut by the general JSON limit (#140 follow-up).

The range invoice list holds up to 5,000 invoices; with the 256 KB JSON limit any export above
about 2,000 rows answered "الطلب كبير جدًا". The export route has its own, still bounded, limit.
"""
import io
import json

from openpyxl import load_workbook

from app.api.app import MAX_EXPORT_JSON, MAX_JSON
from tests.test_api import APICase, H

COLS = [{"label": "رقم", "type": "int"}, {"label": "الوقت", "type": "text"}, {"label": "العميل", "type": "text"},
        {"label": "الأصناف", "type": "int"}] + [{"label": n, "type": "money"} for n in
                                                ("الإجمالي", "نقدي", "بطاقة", "تحويل", "آجل", "مرتجع")] + \
       [{"label": "الحالة", "type": "text"}]


def invoices(n: int) -> bytes:
    """What the invoice list screen sends: JSON.stringify writes Arabic as UTF-8, not \\u escapes."""
    rows = [[100000 + i, "2026-10-14 13:05", "عميل تجربة رقم %d" % i, 3, 125.5, 125.5, 0, 0, 0, 0, "مكتملة"]
            for i in range(n)]
    spec = {"filename": "فواتير البيع", "title": "صيدلية تجربة — فواتير البيع", "subtitle": "أكتوبر",
            "sheets": [{"name": "الفواتير", "columns": COLS, "rows": rows}]}
    return json.dumps(spec, ensure_ascii=False).encode("utf-8")


class TestExportSize(APICase):
    def post(self, raw: bytes, path="/api/export/xlsx"):
        return self.c.post(path, content=raw, headers={**H, "Content-Type": "application/json"})

    def test_a_full_range_of_invoices_exports(self):
        self.http_login()
        raw = invoices(5000)                                  # the most the range invoice list returns
        self.assertGreater(len(raw), MAX_JSON)                # this export failed before the fix
        r = self.post(raw)
        self.assertEqual(r.status_code, 200, r.text[:200])
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws.cell(ws.max_row, 1).value, 104999)

    def test_the_export_limit_still_holds(self):
        self.http_login()
        r = self.post(b"{" + b" " * (MAX_EXPORT_JSON + 10) + b"}")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "too_large")

    def test_other_routes_keep_the_general_limit(self):
        self.http_login()
        r = self.post(b"{" + b" " * (MAX_JSON + 10) + b"}", path="/api/sales")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "too_large")

    def test_signed_out_gets_nothing(self):
        r = self.post(invoices(10))
        self.assertEqual(r.status_code, 401)
