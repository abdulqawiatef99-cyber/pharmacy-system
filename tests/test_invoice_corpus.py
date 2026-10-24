"""Corpus of SYNTHETIC supplier layouts. Each case checks the four layers
separately: extraction (no DB) -> matching -> arithmetic validation -> approval.
Passing here does NOT prove accuracy on real supplier invoices; for that use
scripts/eval_invoices.py on real files (docs/PROGRESS.md)."""
import io
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import Workbook

from app import config
from app.catalog import service as cat
from app.core.errors import Conflict
from app.inventory import service as inv
from app.purchasing.importers import files
from app.purchasing.service import Purchasing
from tests.helpers import DBTestCase

FUT = (date.today() + timedelta(days=700)).strftime("%m/%Y")
SOON = (date.today() + timedelta(days=60)).strftime("%m/%Y")


def xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


# --- layout A: English headers, VAT column, line total printed WITHOUT tax
LAYOUT_A = xlsx([
    ["MIDDLE EAST PHARMA - SALES INVOICE"],
    ["Invoice No:", "ME-5501", "", "Date:", "18/10/2026"],
    ["Customer:", "AbdelKawy Pharmacy"],
    [],
    ["Code", "Description", "Qty", "Bonus", "Public Price", "Disc %", "Net Price", "VAT", "Total", "Batch", "Exp"],
    ["P-101", "PANADOL EXTRA 24 TAB", 10, 0, 56, 18, 45.92, 64.29, 459.20, "PX9", FUT],
    ["S-77", "SHAMPOO ANTI DANDRUFF 200ML", 2, 0, 120, 25, 90, 25.20, 180, "SH1", FUT],
    ["", "Total", "", "", "", "", "", "", 728.69],
])
# --- layout B: Arabic, qty+bonus in one cell, discount written with %, Arabic digits
LAYOUT_B = xlsx([
    ["شركة ابن سينا فارما لتوزيع الأدوية"],
    ["فاتورة رقم", "٧٧٨٨١", "", "التاريخ", "١٩/١٠/٢٠٢٦"],
    [],
    ["الصنف", "الكمية", "سعر الجمهور", "الخصم", "سعر الصيدلي", "الإجمالي", "التشغيلة", "الصلاحية"],
    ["بانادول اكسترا ٢٤ قرص", "10+2", "٥٦", "18%", "45.92", "459.20", "PX9", FUT],
    ["كتافلام ٥٠ مجم ٢٠ قرص", "٥", "٦٠", "١٨٪", "٤٩٫٢", "٢٤٦", "C9", SOON],
    ["الإجمالي", "", "", "", "", "705.20"],
])
# --- layout C: CSV with semicolons, decimal commas, fraction discounts, no batch/expiry
LAYOUT_C = ("Item;Qty;Public Price;Discount;Net Price;Total\n"
            "Cataflam 50mg 20 tab;5;60,00;0,18;49,20;246,00\n"
            "Panadol Extra 24 tab;1;56,00;0,18;45,92;45,92\n"
            "Total;;;;;291,92\n").encode("utf-8")


class CorpusCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        self.ibn = cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما", supplier_type="distributor")
        self.me = cat.create_supplier(self.db, self.ctx, name="Middle East Pharma")
        self.panx = cat.create_item(self.db, self.ctx, {"name_en": "Panadol Extra 24 tab", "name_ar": "بانادول اكسترا 24 قرص",
                                                        "pack_size": 2, "public_price": 5600, "is_drug": 1})
        self.caf = cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 50mg 20 tab", "name_ar": "كتافلام 50 مجم 20 قرص",
                                                       "pack_size": 2, "public_price": 6000, "is_drug": 1})
        self.sham = cat.create_item(self.db, self.ctx, {"name_en": "Anti Dandruff Shampoo 200ml",
                                                        "public_price": 12000, "is_drug": 0, "vat_bp": 1400})

    def issues(self, d, level="error"):
        return {(i["code"], i.get("field")) for i in d["issues"] if i["level"] == level and not i["acknowledged"]}


class TestExtractionLayer(CorpusCase):
    """Layer 1 only: no database involved."""
    def test_layout_a(self):
        ex = files.extract_structured("excel", LAYOUT_A)
        self.assertEqual((ex.invoice_no, ex.invoice_date, ex.declared_total),
                         ("ME-5501", "2026-10-18", Decimal("728.69")))
        l = ex.lines[1]
        self.assertEqual((l.raw_code, l.tax_amount, l.line_total), ("S-77", Decimal("25.20"), Decimal("180")))

    def test_layout_b_arabic_digits_and_qty_plus_bonus(self):
        ex = files.extract_structured("excel", LAYOUT_B)
        self.assertEqual((ex.invoice_no, ex.invoice_date), ("77881", "2026-10-19"))
        self.assertEqual((ex.lines[0].qty, ex.lines[0].bonus_qty), (10, 2))
        self.assertEqual((ex.lines[1].qty, ex.lines[1].public_price, ex.lines[1].discount_pct,
                          ex.lines[1].unit_net_price), (5, Decimal("60"), Decimal("18"), Decimal("49.2")))

    def test_layout_c_csv_semicolon_decimal_comma(self):
        ex = files.extract_structured("csv", LAYOUT_C)
        self.assertIn("discount_fraction_converted", ex.warnings)
        self.assertEqual([(l.qty, l.unit_net_price) for l in ex.lines],
                         [(5, Decimal("49.20")), (1, Decimal("45.92"))])
        self.assertEqual(ex.declared_total, Decimal("291.92"))


class TestPipelineLayers(CorpusCase):
    def test_a_vat_total_excluding_tax_balances(self):
        inv_id = self.p.import_file(self.ctx, LAYOUT_A, "a.xlsx", supplier_id=self.me)
        d = self.p.get(self.ctx, inv_id)
        # layer 2: matching (English invoice spelling vs catalog)
        self.assertEqual(d["lines"][0]["item_id"], self.panx)
        # layer 3: arithmetic — printed totals exclude VAT; computed includes it
        self.assertEqual(d["invoice"]["computed_total"], 72869)
        self.assertNotIn(("total_mismatch", "declared_total"), self.issues(d))

    def test_b_arabic_names_bonus_short_expiry_then_approval(self):
        inv_id = self.p.import_file(self.ctx, LAYOUT_B, "b.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.assertEqual(d["invoice"]["supplier_id"], self.ibn)     # long name contains ours
        self.assertEqual([l["item_id"] for l in d["lines"]], [self.panx, self.caf])
        self.assertIn("short_expiry", {i["code"] for i in d["issues"] if i["level"] == "warning"})
        self.assertEqual(self.issues(d), set(), d["issues"])
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.panx)["total_qty"], 0)   # not yet
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        # layer 4: approval posts (10 + 2 bonus) packs x 2 = 24 strips at 459.20/24
        s = inv.stock_for_item(self.db, self.ctx, self.panx)
        self.assertEqual(s["total_qty"], 24)
        self.assertEqual(Decimal(s["avg_cost"]), (Decimal("459.20") / 24).quantize(Decimal("0.000001")))

    def test_c_missing_batches_are_warnings_not_guesses(self):
        inv_id = self.p.import_file(self.ctx, LAYOUT_C, "c.csv", supplier_id=self.ibn)
        d = self.p.get(self.ctx, inv_id)
        self.assertTrue(all(l["batch_no"] is None and l["expiry_date"] is None for l in d["lines"]))
        self.assertIn("expiry_missing", {i["code"] for i in d["issues"] if i["level"] == "warning"})
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        b = inv.stock_for_item(self.db, self.ctx, self.caf)["batches"]
        self.assertEqual([(x["batch_no"], x["expiry_date"]) for x in b], [(None, None)])   # untracked, not invented

    def test_same_item_twice_same_batch_is_flagged(self):
        data = xlsx([["الصنف", "الكمية", "سعر الجمهور", "الخصم", "الإجمالي", "التشغيلة", "الصلاحية"],
                     ["Cataflam 50mg 20 tab", 2, 60, 18, 98.4, "C9", FUT],
                     ["CATAFLAM 50 MG 20 TAB", 3, 60, 18, 147.6, "c9", FUT],
                     ["الإجمالي", "", "", "", 246]])
        d = self.p.get(self.ctx, self.p.import_file(self.ctx, data, "d.xlsx", supplier_id=self.ibn))
        self.assertIn("duplicate_line", {i["code"] for i in d["issues"]})

    def test_misread_price_is_caught_by_arithmetic(self):
        # net price printed 49.20 but read as 94.20 (digits swapped) -> line total disagrees
        data = xlsx([["الصنف", "الكمية", "سعر الجمهور", "سعر الصيدلي", "الإجمالي"],
                     ["Cataflam 50mg 20 tab", 5, 60, 94.2, 246], ["الإجمالي", "", "", "", 246]])
        d = self.p.get(self.ctx, self.p.import_file(self.ctx, data, "e.xlsx", supplier_id=self.ibn))
        codes = {i["code"] for i in d["issues"]}
        self.assertIn("line_total_mismatch", codes)
        self.assertIn("net_above_public", codes)
        self.assertFalse(d["can_approve"])
        with self.assertRaises(Conflict):
            self.p.approve(self.ctx, d["invoice"]["id"], d["invoice"]["version"])

    def test_empty_and_header_only_files(self):
        d = self.p.get(self.ctx, self.p.import_file(self.ctx, xlsx([["الصنف", "الكمية", "الإجمالي"]]),
                                                     "f.xlsx", supplier_id=self.ibn))
        self.assertIn("no_lines", {i["code"] for i in d["issues"]})
        self.assertFalse(d["can_approve"])


class TestEvalHarness(CorpusCase):
    def test_harness_scores_and_flags_confident_errors(self):
        import json as _json
        import sys
        sys.path.insert(0, "scripts")
        import eval_invoices
        folder = self.tmp / "real"
        folder.mkdir()
        (folder / "b.xlsx").write_bytes(LAYOUT_B)
        (folder / "b.expected.json").write_text(_json.dumps({
            "invoice_no": "77881", "declared_total": "705.20",
            "lines": [{"name": "بانادول اكسترا ٢٤ قرص", "qty": 10, "bonus_qty": 2, "line_total": "459.20",
                       "batch_no": "PX9", "expiry": FUT},
                      {"name": "كتافلام ٥٠ مجم ٢٠ قرص", "qty": 6, "unit_net_price": "49.20"}]}), encoding="utf-8")
        import contextlib
        import io as _io
        with contextlib.redirect_stdout(_io.StringIO()):
            s = eval_invoices.main([str(folder), "--no-ai", "--json", str(self.tmp / "r.json")])
        self.assertEqual(s["scored"], 1)
        # one expected value (qty 6) deliberately differs from the file -> 1 confident error
        self.assertEqual(s["wrong_but_confident"], 1)
        self.assertAlmostEqual(s["mean_line_field_accuracy"], 6 / 7, places=3)


class TestProviders(CorpusCase):
    def test_token_and_cost_estimates_follow_docs(self):
        from app.purchasing.extractors import estimate_cost_usd, image_tokens
        self.assertEqual(image_tokens(1000, 1000), 1296)                  # example from vision docs
        self.assertLessEqual(image_tokens(3000, 4000), 4784)               # capped at high-res limit
        c = estimate_cost_usd("claude-sonnet-5-5", 5500, 2500)
        self.assertAlmostEqual(c, 5500 / 1e6 * 2 + 2500 / 1e6 * 10)
        self.assertIsNone(estimate_cost_usd("unknown-model", 1, 1))

    def test_disabled_provider_is_explicit(self):
        import os
        from app.core import accounts
        from app.purchasing.importers.ai_vision import AIUnavailable
        os.environ["PHARM_OCR_PROVIDER"] = "none"
        try:
            accounts.set_entitlement(self.db, self.ctx.org_id, "ai_extraction")
            p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u2"))
            with self.assertRaises(AIUnavailable):
                p.import_file(self.ctx, b"\xff\xd8\xff\xe0" + b"0" * 500, "photo.jpg")
        finally:
            os.environ.pop("PHARM_OCR_PROVIDER")
        self.assertEqual(self.db.one("SELECT count(*) FROM purchase_invoices")[0], 0)
        self.assertIsNone(self.db.one("SELECT used FROM usage_counters WHERE feature='ai_extraction'"))

    def test_usage_recorded_from_response(self):
        from app.purchasing.importers import ai_vision
        from tests.test_purchasing import AI_FIXTURE
        resp = {**AI_FIXTURE, "usage": {"input_tokens": 5100, "output_tokens": 900}}
        ex = ai_vision.parse_response(resp)
        self.assertEqual((ex.usage["input_tokens"], ex.usage["output_tokens"]), (5100, 900))
