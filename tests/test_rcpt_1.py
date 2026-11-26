"""Structured and raster coverage for the thermal receipt, using synthetic sales."""
import base64
import io
import unittest

from PIL import Image, ImageChops

from app.printing import barcode, escpos, settings
from app.core.errors import ValidationFailed
from tests.helpers import DBTestCase


def sample_sale(name="Sample Relief 24 tablets"):
    return {
        "sale_no": 12048, "kind": "store", "subtotal": 5000, "discount_total": 0,
        "total": 5000, "paid_cash": 5000, "rounding": 0,
        "lines": [{
            "item_id": "sample-item",
            "item": {"name": name, "pack_size": 4, "base_unit": "شريط", "pack_unit": "علبة"},
            "qty_base": 4, "gross": 5000, "line_total": 5000,
            "official_prices": [{"pack": True, "qty": 1, "price": 5000, "gross": 5000}],
        }],
    }


def rows(sale, cfg=None, **kwargs):
    return escpos.receipt_lines("صيدلية المثال", sale, "2026-11-23 14:05", cfg, **kwargs)


class ReceiptContentTests(unittest.TestCase):
    def test_cash_receipt(self):
        out = rows(sample_sale(), {"lines": ["شارع المثال", "01000000000"], "header": "خدمة التوصيل"},
                   seller="بائع تجريبي", cashier="أمين صندوق تجريبي", given=6000, return_days=14)
        self.assertEqual(out[0], ("title", "صيدلية المثال", ""))
        self.assertIn(("small", "فاتورة رقم 12048", "2026-11-23 14:05"), out)
        for label in ("البائع: بائع تجريبي", "الكاشير: أمين صندوق تجريبي", "الدفع: كاش"):
            self.assertIn(("small", label, ""), out)
        self.assertIn(("item", "Sample Relief 24 tablets", ("1", "1 علبة", "50.00", "50.00")), out)
        self.assertIn(("small", "عدد الأصناف: 1", ""), out)
        self.assertIn(("small", "عدد القطع: 1", ""), out)
        self.assertIn(("total", "المطلوب", "50.00"), out)
        self.assertIn(("row", "مدفوع كاش", "50.00"), out)
        self.assertIn(("total", "الباقي", "10.00"), out)
        self.assertIn(("barcode", "12048", ""), out)
        self.assertEqual(out[-1], ("center", "شكرًا لزيارتكم", ""))

    def test_saved_public_prices_separate_box_strip_and_different_prices(self):
        sale = sample_sale()
        sale["lines"][0].update(qty_base=9, gross=12250, line_total=12250, official_prices=[
            {"pack": True, "qty": 1, "price": 5000, "gross": 5000},
            {"pack": False, "qty": 1, "price": 1250, "gross": 1250},
            {"pack": True, "qty": 1, "price": 6000, "gross": 6000},
        ])
        sale.update(subtotal=12250, total=12250, paid_cash=12250)
        out = rows(sale)
        self.assertEqual([left for kind, _, left in out if kind == "item"], [
            ("1", "1 علبة", "50.00", "50.00"),
            ("2", "1 شريط", "12.50", "12.50"),
            ("3", "1 علبة", "60.00", "60.00"),
        ])
        self.assertIn(("small", "عدد الأصناف: 1", ""), out)
        self.assertIn(("small", "عدد القطع: 3", ""), out)

    def test_discounts_and_rounding(self):
        sale = sample_sale()
        sale["lines"][0].update(discount_bp=1000, discount=900, invoice_share=400, line_total=4100)
        sale.update(discount_total=900, rounding=-10, total=4100, paid_cash=4090)
        out = rows(sale)
        self.assertIn(("detail", "خصم 10% −5.00", ""), out)
        self.assertIn(("row", "الإجمالي قبل الخصم", "50.00"), out)
        self.assertIn(("row", "خصم الأصناف", "5.00"), out)
        self.assertIn(("row", "خصم الفاتورة", "4.00"), out)
        self.assertIn(("row", "التقريب", "-0.10"), out)
        self.assertIn(("total", "المطلوب", "40.90"), out)
        self.assertIn(("center", "وفّرت: 9.00 ج", ""), out)
        compact = rows(sale, {"template": "compact"})
        self.assertFalse(any(kind == "detail" for kind, *_ in compact))
        self.assertIn(("row", "خصم الأصناف", "5.00"), compact)

    def test_credit_customer_and_balances_even_when_customer_setting_off(self):
        sale = sample_sale()
        sale.update(paid_cash=0, on_credit=5000, customer={
            "name": "عميل تجريبي", "estock_code": 35, "phone": "01000000000",
            "balance_before": 1200, "balance": 6200,
        })
        out = rows(sale, {"show_customer": False})
        self.assertIn(("small", "الدفع: آجل", ""), out)
        self.assertIn(("small", "العميل: عميل تجريبي (كود 35)", ""), out)
        self.assertIn(("small", "موبايل: 01000000000", ""), out)
        self.assertIn(("center", "هذه الفاتورة على الحساب", ""), out)
        self.assertIn(("row", "الرصيد السابق", "12.00"), out)
        self.assertIn(("row", "الرصيد الحالي", "62.00"), out)

    def test_delivery_uses_destination_and_lists_payment_method(self):
        sale = sample_sale()
        sale.update(kind="delivery", paid_cash=0, paid_transfer=5200, total=5200, delivery_fee=200,
                    customer={"name": "عميل تجريبي", "address1": "العنوان المسجل"},
                    delivery={"address": "عنوان التوصيل", "phone": "01000000001"},
                    payments=[{"method": "transfer", "channel": "محفظة تجربة", "amount": 5200}])
        out = rows(sale, {"show_customer": False})
        self.assertIn(("small", "الدفع: توصيل", ""), out)
        self.assertIn(("small", "العنوان: عنوان التوصيل", ""), out)
        self.assertIn(("small", "موبايل: 01000000001", ""), out)
        self.assertIn(("row", "رسوم التوصيل", "2.00"), out)
        self.assertIn(("row", "مدفوع محفظة · محفظة تجربة", "52.00"), out)

    def test_cash_delivery_shows_cash_due_until_customer_receives_order(self):
        sale = sample_sale()
        sale.update(kind="delivery", delivery={"address": "عنوان التوصيل", "status": "out"})
        self.assertIn(("row", "كاش عند الاستلام", "50.00"), rows(sale))
        self.assertNotIn(("row", "مدفوع كاش", "50.00"), rows(sale))
        sale["delivery"]["status"] = "delivered"
        self.assertIn(("row", "مدفوع كاش", "50.00"), rows(sale))


@unittest.skipUnless(escpos.shaping_available(), "The thermal raster requires libraqm")
class ReceiptRasterTests(unittest.TestCase):
    def test_widths_and_templates(self):
        for width in (384, 576):
            heights = {}
            for template in ("detailed", "compact", "large"):
                with self.subTest(width=width, template=template):
                    image = escpos.render_receipt(rows(sample_sale()), width, template=template)
                    self.assertEqual(image.width, width)
                    bounds = ImageChops.invert(image).getbbox()
                    self.assertGreaterEqual(bounds[0], 4)
                    self.assertLessEqual(bounds[2], width - 4)
                    heights[template] = image.height
            self.assertLess(heights["compact"], heights["detailed"])
            self.assertLess(heights["detailed"], heights["large"])

    def test_long_name_wraps_including_single_long_word(self):
        short = escpos.render_receipt(rows(sample_sale()), 384)
        long = escpos.render_receipt(rows(sample_sale("Arabic Sample " + "طويل" * 35 + " Final Name")), 384)
        self.assertGreater(long.height, short.height + 100)
        self.assertLessEqual(ImageChops.invert(long).getbbox()[2], 380)

    def test_barcode_matches_invoice_number_at_both_widths(self):
        bits = barcode.code128_modules("12048")
        for width in (384, 576):
            image = escpos.render_receipt([("barcode", "12048", "")], width)
            row = "".join("1" if pixel == 0 else "0" for pixel in image.crop((0, 15, width, 16)).getdata())
            bars = row.strip("0")
            module = len(bars) // len(bits)
            self.assertGreaterEqual(module, 2)
            self.assertEqual(bars, "".join(bit * module for bit in bits))
            self.assertGreaterEqual(row.index("1"), 10 * module)

class ReceiptSettingsTests(DBTestCase):
    def test_templates_persist_and_invalid_values_are_rejected(self):
        self.assertEqual(settings.get(self.db, self.ctx.org_id)["template"], "detailed")
        self.assertEqual(settings.save(self.db, self.ctx, {"template": "large"})["template"], "large")
        with self.assertRaises(ValidationFailed):
            settings.save(self.db, self.ctx, {"template": "invalid"})

    @unittest.skipUnless(escpos.shaping_available(), "The thermal raster requires libraqm")
    def test_preview_uses_unsaved_width_and_template_without_persisting(self):
        before = settings.get(self.db, self.ctx.org_id)
        audits = self.db.one("SELECT count(*) FROM audit_log")[0]
        previews = []
        for template in ("compact", "large"):
            result = settings.save(self.db, self.ctx, {"preview": True, "paper": 58, "template": template})
            with Image.open(io.BytesIO(base64.b64decode(result["png"]))) as png:
                self.assertEqual((png.width, png.mode), (384, "1"))
                previews.append(png.height)
        self.assertLess(*previews)
        self.assertEqual(settings.get(self.db, self.ctx.org_id), before)
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log")[0], audits)


class ReceiptHeaderFailureTests(DBTestCase):
    def test_any_logo_or_qr_error_still_prints_the_receipt(self):
        """A broken logo or QR (any error, e.g. PIL's DecompressionBombError) is skipped, never a failed print."""
        from unittest import mock
        from app.printing import service
        cfg = dict(settings.get(self.db, self.ctx.org_id), qr_text="https://example.invalid")
        # the raster itself is not under test (it needs raqm, missing on some runners): capture what reaches it
        with mock.patch.object(escpos, "receipt_header", side_effect=RuntimeError("bad logo")), \
             mock.patch.object(escpos, "receipt_footer_qr", side_effect=RuntimeError("bad qr")), \
             mock.patch.object(escpos, "render_receipt", return_value="image") as render:
            image = service.receipt_image("صيدلية المثال", sample_sale(), "2026-11-23 14:05", cfg, logo_png=b"x")
        self.assertEqual(image, "image")
        self.assertIsNone(render.call_args.kwargs["header"])
        self.assertEqual(render.call_args.kwargs["footer_codes"], ())
