import io
import json
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app import config
from app.catalog import matching, service as cat
from app.core import accounts
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.inventory import service as inv
from app.purchasing import validation
from app.purchasing.importers import ai_vision, files
from app.purchasing.importers.table import grid_to_invoice
from app.purchasing.model import parse_decimal, parse_expiry, parse_int
from app.purchasing.service import Purchasing
from tests.fixtures.make_invoices import csv_invoice_fraction_discounts, excel_invoice
from tests.helpers import DBTestCase

FUT = (date.today() + timedelta(days=700)).strftime("%m/%Y")


class TestParsers(unittest.TestCase):
    def test_decimal(self):
        self.assertEqual(parse_decimal("1,234.50"), Decimal("1234.50"))
        self.assertEqual(parse_decimal("١٢٣٫٥"), Decimal("123.5"))
        self.assertEqual(parse_decimal("12,5"), Decimal("12.5"))
        self.assertEqual(parse_decimal("1,234"), Decimal("1234"))
        self.assertEqual(parse_decimal("45.00 ج.م"), Decimal("45.00"))
        self.assertIsNone(parse_decimal("abc"))
        self.assertIsNone(parse_int("2.5"))      # never round a pack quantity silently

    def test_expiry(self):
        self.assertEqual(parse_expiry("05/2027"), ("2027-05-31", "month"))
        self.assertEqual(parse_expiry("2-28"), ("2028-02-29", "month"))
        self.assertEqual(parse_expiry("2027-11"), ("2027-11-30", "month"))
        self.assertEqual(parse_expiry("15/03/2027"), ("2027-03-15", "day"))
        self.assertEqual(parse_expiry("MAY-27"), ("2027-05-31", "month"))
        self.assertEqual(parse_expiry("Exp: ٠٦/٢٠٢٨"), ("2028-06-30", "month"))
        self.assertEqual(parse_expiry("13/2027"), (None, None))
        self.assertEqual(parse_expiry("31/02/2027"), (None, None))
        self.assertEqual(parse_expiry("soon"), (None, None))


class TestTableImporter(unittest.TestCase):
    def test_excel_arabic_layout(self):
        data = excel_invoice([("Panadol 500mg 24 tab", 10, 2, 40, 20, 32, 320, "p1", FUT, "6221")])
        kind, _ = files.sniff(data, "x.xlsx")
        ex = files.extract_structured(kind, data)
        self.assertEqual((ex.invoice_no, ex.invoice_date, ex.declared_total),
                         ("INV-1001", "2026-10-20", Decimal("320")))
        self.assertEqual(ex.supplier_name, "ابن سينا فارما")
        l = ex.lines[0]
        self.assertEqual((l.qty, l.bonus_qty, l.public_price, l.discount_pct, l.batch_no),
                         (10, 2, Decimal("40"), Decimal("20"), "p1"))

    def test_csv_fraction_discount_column(self):
        ex = files.extract_structured("csv", csv_invoice_fraction_discounts())
        self.assertIn("discount_fraction_converted", ex.warnings)
        self.assertEqual([l.discount_pct for l in ex.lines], [Decimal("20.00"), Decimal("18.00")])
        self.assertEqual(ex.declared_total, Decimal("566.00"))
        self.assertEqual(ex.invoice_no, "C-77")

    def test_unreadable_cell_is_flagged_not_guessed(self):
        ex = grid_to_invoice([["الصنف", "الكمية", "سعر الجمهور", "الاجمالي"],
                              ["Panadol 500mg 24 tab", "1O", "40", "400"]])
        self.assertIsNone(ex.lines[0].qty)
        self.assertEqual(ex.lines[0].confidence["qty"], 0.0)

    def test_header_not_found(self):
        ex = grid_to_invoice([["a", "b"], [1, 2]])
        self.assertIn("header_not_found", ex.warnings)

    def test_sniff_rejects_unknown(self):
        with self.assertRaises(ValidationFailed):
            files.sniff(b"\x00\x01\x02binary", "x.bin")
        with self.assertRaises(ValidationFailed):
            files.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100, "old.xls")


class TestValidationUnit(unittest.TestCase):
    def test_derive_net_and_total(self):
        l = validation.derive_line({"public_price": 4000, "discount_bp": 2000, "qty": 10,
                                    "field_sources": {}, "field_confidence": {}})
        self.assertEqual((l["unit_net_price"], l["line_total"]), (3200, 32000))
        self.assertEqual(l["field_sources"]["unit_net_price"], "inferred")

    def test_total_printed_without_tax(self):
        l = {"unit_net_price": 1000, "qty": 10, "tax_amount": 1400, "line_total": 10000}
        self.assertEqual(validation.effective_amount(l), 11400)


# Hand-written example of the expected model output. NOT a real API response.
AI_FIXTURE = {
    "model": "fixture", "stop_reason": "end_turn",
    "content": [{"type": "text", "text": json.dumps({
        "supplier_name": "Pharma Overseas", "invoice_no": "PO-55", "invoice_date": "21/10/2026",
        "declared_total": 640, "header_confidence": {"supplier_name": 0.97, "invoice_no": 0.99, "declared_total": 0.95},
        "lines": [
            {"raw_name": "PANADOL 500MG 24 TAB", "qty": 20, "bonus_qty": 0, "public_price": 40,
             "discount_pct": 20, "unit_net_price": 32, "line_total": 640, "batch_no": "X1",
             "expiry": FUT, "confidence": {"raw_name": 0.98, "qty": 0.6, "bonus_qty": 0.9,
                                           "public_price": 0.97,
                                           "discount_pct": 0.95, "unit_net_price": 0.95,
                                           "line_total": 0.97, "batch_no": 0.9, "expiry": 0.9}}],
        "notes": ["qty looks handwritten"]})}]}


class TestAIParse(unittest.TestCase):
    def test_parse_and_cap_confidence(self):
        ex = ai_vision.parse_response(AI_FIXTURE)
        self.assertEqual(ex.method, "ai_vision")
        self.assertEqual(ex.invoice_date, "2026-10-21")
        l = ex.lines[0]
        self.assertEqual(l.sources["qty"], "ai")
        self.assertEqual(l.confidence["qty"], 0.6)
        self.assertLessEqual(max(l.confidence.values()), ai_vision.AI_CONFIDENCE_CAP)
        self.assertTrue(any(w.startswith("ai_note") for w in ex.warnings))

    def test_bad_output(self):
        with self.assertRaises(ValidationFailed):
            ai_vision.parse_response({"content": [{"type": "text", "text": "sorry"}]})
        with self.assertRaises(ValidationFailed):
            ai_vision.parse_response({"content": [], "stop_reason": "max_tokens"})

    def test_no_key_is_explicit(self):
        with self.assertRaises(ai_vision.AIUnavailable):
            ai_vision.call_api(None, {})


class PurchasingCase(DBTestCase):
    def setUp(self):
        super().setUp()
        s = config.load()
        self.settings = replace(s, upload_dir=self.tmp / "uploads", anthropic_api_key=None)
        self.ai_calls = []

        def fake_ai(data, mime):
            self.ai_calls.append(mime)
            return ai_vision.parse_response(AI_FIXTURE)
        self.p = Purchasing(self.db, self.settings, ai_extract=fake_ai)
        self.sup = cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما",
                                       supplier_type="distributor")
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab",
                                                       "pack_size": 2, "public_price": 3800,
                                                       "is_drug": 1})
        self.cat50 = cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 50mg 20 tab",
                                                         "public_price": 6000})

    def std_invoice(self, **kw):
        return excel_invoice([
            ("PANADOL 500 MG 24 TAB", 10, 2, 40, 20, 32, 320, "P1", FUT, None),
            ("Cataflam 50mg 20 tab", 5, 0, 60, 18, 49.2, 246, "C9", FUT, "6221025099998"),
        ], **kw)

    def codes(self, data, level="error"):
        return {i["code"] for i in data["issues"] if i["level"] == level and not i["acknowledged"]}


class TestPurchaseFlow(PurchasingCase):
    def test_catalog_price_writes_require_price_authority(self):
        intake = replace(self.ctx, permissions=self.ctx.permissions - {"items.price"}, role_code="pharmacist")
        with self.assertRaises(Forbidden):
            cat.create_item(self.db, intake, {"name_en": "Synthetic Price Check", "public_price": 100})
        draft = self.p.import_file(intake, self.std_invoice(), "fixture.xlsx")
        invoice = self.p.get(intake, draft)["invoice"]
        self.p.approve(intake, draft, invoice["version"])
        self.assertEqual(cat.get_item(self.db, self.ctx, self.pan)["public_price"], 3800)
        batch = self.db.one("SELECT public_price FROM batches WHERE org_id=? AND item_id=?",
                            (self.ctx.org_id, self.pan))
        self.assertIsNone(batch["public_price"])
        cat.update_item(self.db, self.ctx, self.pan, {"public_price": 3950})
        self.assertIsNone(self.db.one("SELECT public_price FROM batches WHERE org_id=? AND item_id=?",
                                      (self.ctx.org_id, self.pan))["public_price"])
        self.assertEqual(self.db.one("SELECT count(*) n FROM item_price_history"
                                     " WHERE org_id=? AND item_id=?", (self.ctx.org_id, self.pan))["n"], 2)

    def test_full_happy_path(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(), "inv.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.assertEqual(d["invoice"]["supplier_id"], self.sup)       # resolved from sheet
        self.assertEqual(d["invoice"]["computed_total"], 56600)
        self.assertEqual(d["invoice"]["declared_total"], 56600)
        self.assertEqual([l["item_id"] for l in d["lines"]], [self.pan, self.cat50])
        self.assertEqual(self.codes(d), set(), d["issues"])
        self.assertIn("public_price_changed", self.codes(d, "warning"))
        self.assertTrue(d["can_approve"])
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 0)

        d = self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        self.assertEqual(d["invoice"]["status"], "approved")
        s = inv.stock_for_item(self.db, self.ctx, self.pan)
        # (10 paid + 2 bonus) packs x 2 strips = 24 strips; cost 320 EGP / 24 strips
        self.assertEqual(s["total_qty"], 24)
        self.assertEqual(Decimal(s["avg_cost"]), (Decimal("320") / 24).quantize(Decimal("0.000001")))
        self.assertEqual(s["batches"][0]["batch_no"], "P1")
        self.assertEqual(cat.get_item(self.db, self.ctx, self.pan)["public_price"], 4000)
        hist = self.db.all("SELECT previous_price, public_price, source FROM item_price_history"
                           " WHERE item_id=? ORDER BY observed_at", (self.pan,))
        self.assertEqual(tuple(hist[-1]), (3800, 4000, "purchase_invoice"))
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])
        with self.assertRaises(Conflict):     # approved invoice is immutable
            self.p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"qty": 1}, None)

    def test_duplicate_file_rejected(self):
        data = self.std_invoice()
        self.p.import_file(self.ctx, data, "a.xlsx")
        with self.assertRaises(Conflict) as e:
            self.p.import_file(self.ctx, data, "copy.xlsx")
        self.assertEqual(e.exception.code, "duplicate_file")

    def test_duplicate_invoice_number_blocks(self):
        a = self.p.import_file(self.ctx, self.std_invoice(), "a.xlsx")
        other = excel_invoice([("Cataflam 50mg 20 tab", 1, 0, 60, 18, 49.2, 49.2, "C9", FUT, None)])
        with self.assertRaises(Conflict) as e:          # same supplier + same number
            self.p.import_file(self.ctx, other, "b.xlsx")
        self.assertEqual(e.exception.code, "duplicate_invoice")
        # renaming a second invoice to an existing number is also refused
        b = self.p.import_file(self.ctx, excel_invoice(
            [("Cataflam 50mg 20 tab", 1, 0, 60, 18, 49.2, 49.2, "C9", FUT, None)],
            invoice_no="INV-2"), "c.xlsx")
        d = self.p.get(self.ctx, b)
        with self.assertRaises(Conflict):
            self.p.update_header(self.ctx, b, {"invoice_no": "INV-1001"}, d["invoice"]["version"])
        self.assertTrue(a)

    def test_misread_quantity_detected_then_fixed(self):
        data = excel_invoice([("PANADOL 500 MG 24 TAB", 100, 0, 40, 20, 32, 320, "P1", FUT, None)],
                             total=320)
        inv_id = self.p.import_file(self.ctx, data, "x.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.assertIn("line_total_mismatch", self.codes(d))
        self.assertFalse(d["can_approve"])
        with self.assertRaises(Conflict):
            self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        d = self.p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"qty": 10},
                               d["invoice"]["version"])
        self.assertEqual(self.codes(d), set())
        self.assertTrue(d["can_approve"])

    def test_total_mismatch_needs_written_ack(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(total=570), "x.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.assertIn("total_mismatch", self.codes(d))
        with self.assertRaises(ValidationFailed):
            self.p.acknowledge(self.ctx, inv_id, "total_mismatch", "x", None, d["invoice"]["version"])
        d = self.p.acknowledge(self.ctx, inv_id, "total_mismatch", "فرق تقريب من المورد",
                               None, d["invoice"]["version"])
        self.assertTrue(d["can_approve"])
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])

    def test_stale_version(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(), "x.xlsx")
        d = self.p.get(self.ctx, inv_id)
        v = d["invoice"]["version"]
        self.p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"batch_no": "p2"}, v)
        with self.assertRaises(Conflict) as e:
            self.p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"qty": 3}, v)
        self.assertEqual(e.exception.code, "stale_version")

    def test_unmatched_line_and_new_item(self):
        data = excel_invoice([("Zyrtec 10mg 20 tab", 2, 0, 50, 15, 42.5, 85, "Z1", FUT,
                               "6221000000017")])
        inv_id = self.p.import_file(self.ctx, data, "x.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.assertIn("unmatched", self.codes(d))
        d = self.p.create_item_from_line(self.ctx, inv_id, d["lines"][0]["id"], {"pack_size": 2},
                                         d["invoice"]["version"])
        self.assertEqual(self.codes(d), set())
        item_id = d["lines"][0]["item_id"]
        self.assertEqual(cat.get_item(self.db, self.ctx, item_id)["barcodes"], ["6221000000017"])

    def test_suggested_match_must_be_confirmed_and_is_learned(self):
        data = excel_invoice([("CATAFLAM 20 TAB", 2, 0, 60, 18, 49.2, 98.4, "C1", FUT, None)])
        inv_id = self.p.import_file(self.ctx, data, "x.xlsx")
        d = self.p.get(self.ctx, inv_id)
        line = d["lines"][0]
        self.assertEqual(line["match_status"], "suggested")
        self.assertIn("match_unconfirmed", self.codes(d))
        d = self.p.update_line(self.ctx, inv_id, line["id"], {"item_id": self.cat50},
                               d["invoice"]["version"])
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        r = matching.match_line(self.db, self.ctx.org_id, raw_name="CATAFLAM 20 TAB",
                                supplier_id=self.sup)
        self.assertEqual((r.status, r.method, r.item_id), ("auto", "alias", self.cat50))

    def test_expired_item_blocks(self):
        past = (date.today() - timedelta(days=40)).strftime("%m/%Y")
        data = excel_invoice([("PANADOL 500 MG 24 TAB", 1, 0, 40, 20, 32, 32, "P1", past, None)])
        d = self.p.get(self.ctx, self.p.import_file(self.ctx, data, "x.xlsx"))
        self.assertIn("expired", self.codes(d))

    def test_assistant_cannot_approve(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        inv_id = self.p.import_file(actx, self.std_invoice(), "x.xlsx")   # can enter
        d = self.p.get(actx, inv_id)
        with self.assertRaises(Forbidden):
            self.p.approve(actx, inv_id, d["invoice"]["version"])

    def test_cancel_approved_reverses_stock(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(), "x.xlsx")
        d = self.p.approve(self.ctx, inv_id, self.p.get(self.ctx, inv_id)["invoice"]["version"])
        self.p.cancel(self.ctx, inv_id, "فاتورة مرتجعة بالكامل", d["invoice"]["version"])
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 0)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])
        self.p.import_file(self.ctx, self.std_invoice(), "x.xlsx")   # re-import allowed

    def test_cannot_cancel_after_stock_sold(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(), "x.xlsx")
        d = self.p.approve(self.ctx, inv_id, self.p.get(self.ctx, inv_id)["invoice"]["version"])
        batch = self.db.one("SELECT batch_id FROM stock_movements WHERE item_id=?", (self.pan,))[0]
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=-5, batch_id=batch,
                              movement_type="sale")
        with self.assertRaises(Conflict) as e:
            self.p.cancel(self.ctx, inv_id, "إلغاء تجريبي", d["invoice"]["version"])
        self.assertEqual(e.exception.code, "insufficient_stock")
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 19)

    def test_tenant_isolation(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(), "x.xlsx")
        file_id = self.p.get(self.ctx, inv_id)["invoice"]["source_file_id"]
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            self.p.get(other, inv_id)
        with self.assertRaises(NotFound):
            self.p.get_file(other, file_id)
        with self.assertRaises(NotFound):
            self.p.approve(other, inv_id, None)
        self.assertEqual(self.p.list(other), [])

    def test_edits_are_audited_and_original_kept(self):
        inv_id = self.p.import_file(self.ctx, self.std_invoice(), "x.xlsx")
        d = self.p.get(self.ctx, inv_id)
        self.p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"batch_no": "zz"},
                           d["invoice"]["version"])
        rows = self.db.all("SELECT action, before_json FROM audit_log"
                           " WHERE entity_id=? ORDER BY at", (inv_id,))
        self.assertEqual([r["action"] for r in rows], ["purchase.import", "purchase.line_edit"])
        self.assertIn('"P1"', rows[1]["before_json"])
        raw = json.loads(self.db.one("SELECT raw_extraction FROM purchase_invoices WHERE id=?",
                                     (inv_id,))[0])
        self.assertEqual(raw["lines"][0]["batch_no"], "P1")


class TestAIFlow(PurchasingCase):
    JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200

    def test_photo_requires_ai_entitlement(self):
        with self.assertRaises(Forbidden):
            self.p.import_file(self.ctx, self.JPEG, "photo.jpg")
        self.assertEqual(self.ai_calls, [])

    def test_low_confidence_blocks_until_confirmed(self):
        accounts.set_entitlement(self.db, self.ctx.org_id, "ai_extraction", limits={"monthly": 50})
        cat.create_supplier(self.db, self.ctx, name="Pharma Overseas")
        inv_id = self.p.import_file(self.ctx, self.JPEG, "photo.jpg")
        d = self.p.get(self.ctx, inv_id)
        self.assertEqual(d["invoice"]["extraction_method"], "ai_vision")
        lc = [i for i in d["issues"] if i["code"] == "low_confidence"]
        self.assertEqual([i["field"] for i in lc], ["qty"])
        d = self.p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"confirm_fields": ["qty"]},
                               d["invoice"]["version"])
        self.assertEqual(self.codes(d), set())
        used = self.db.one("SELECT used FROM usage_counters WHERE feature='ai_extraction'")[0]
        self.assertEqual(used, 1)

    def test_editing_a_low_confidence_discount_clears_it(self):
        accounts.set_entitlement(self.db, self.ctx.org_id, "ai_extraction")
        fixture = json.loads(AI_FIXTURE["content"][0]["text"])
        fixture["lines"][0]["confidence"]["discount_pct"] = 0.4
        fixture["lines"][0]["confidence"]["qty"] = 0.95
        p = Purchasing(self.db, self.settings, ai_extract=lambda d, m: ai_vision.from_json(fixture, "f"))
        inv_id = p.import_file(self.ctx, self.JPEG, "photo.jpg")
        d = p.get(self.ctx, inv_id)
        self.assertEqual([i["field"] for i in d["issues"] if i["code"] == "low_confidence"],
                         ["discount_bp"])
        d = p.update_line(self.ctx, inv_id, d["lines"][0]["id"], {"discount_bp": 2000},
                          d["invoice"]["version"])
        self.assertNotIn("low_confidence", self.codes(d))

    def test_real_extractor_without_key_fails_clearly(self):
        accounts.set_entitlement(self.db, self.ctx.org_id, "ai_extraction")
        p = Purchasing(self.db, self.settings)   # real extractor, no API key configured
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (800, 1000), "white").save(buf, "JPEG")
        with self.assertRaises(ai_vision.AIUnavailable):
            p.import_file(self.ctx, buf.getvalue(), "photo.jpg")
        self.assertEqual(self.db.one("SELECT count(*) FROM purchase_invoices")[0], 0)


if __name__ == "__main__":
    unittest.main()


try:
    import reportlab  # noqa: F401  (test-only: builds the sample PDF)
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


@unittest.skipUnless(HAS_REPORTLAB, "reportlab not installed (only needed to build the sample PDF)")
class TestPdfText(PurchasingCase):
    def make_pdf(self):
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, Paragraph, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        st = getSampleStyleSheet()
        data = [["Item Name", "Qty", "Bonus", "Public Price", "Disc %", "Net Price", "Total", "Batch", "Exp"],
                ["PANADOL 500 MG 24 TAB", "10", "0", "40.00", "20", "32.00", "320.00", "P1", FUT],
                ["Cataflam 50mg 20 tab", "5", "0", "60.00", "18", "49.20", "246.00", "C9", FUT],
                ["Total", "", "", "", "", "", "566.00", "", ""]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        doc.build([Paragraph("Invoice No: PDF-9", st["Normal"]),
                   Paragraph("Supplier: Pharma Overseas", st["Normal"]), t])
        return buf.getvalue()

    def test_text_pdf_imported_without_ai(self):
        cat.create_supplier(self.db, self.ctx, name="Pharma Overseas")
        inv_id = self.p.import_file(self.ctx, self.make_pdf(), "inv.pdf")
        d = self.p.get(self.ctx, inv_id)
        self.assertEqual(d["invoice"]["extraction_method"], "pdf_text")
        self.assertEqual(d["invoice"]["invoice_no"], "PDF-9")
        self.assertEqual(d["invoice"]["declared_total"], 56600)
        self.assertEqual(len(d["lines"]), 2)
        self.assertEqual(self.ai_calls, [])
        self.assertEqual(self.codes(d), set(), d["issues"])


class TestManualEntry(PurchasingCase):
    def test_typed_invoice_lines_and_approve(self):
        sup = cat.create_supplier(self.db, self.ctx, name="ابن سينا")
        inv = self.p.create_manual(self.ctx, supplier_id=sup)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        d = self.p.add_line(self.ctx, inv, {"raw_name": "سطر لسه بيتكتب"}, v)      # no qty yet
        item = self.db.one("SELECT id FROM items LIMIT 1")[0]
        d = self.p.add_line(self.ctx, inv, {"item_id": item, "qty": 5, "public_price": 4000,
                                            "discount_bp": 2000, "expiry_date": FUT}, d["invoice"]["version"])
        line = d["lines"][-1]
        self.assertEqual((line["unit_net_price"], line["line_total"]), (3200, 16000))
        self.p.delete_line(self.ctx, inv, d["lines"][0]["id"], d["invoice"]["version"])
        d = self.p.get(self.ctx, inv)
        d = self.p.update_header(self.ctx, inv, {"invoice_no": "M-1", "declared_total": 16000},
                                 d["invoice"]["version"])
        self.assertTrue(d["can_approve"], d["issues"])
