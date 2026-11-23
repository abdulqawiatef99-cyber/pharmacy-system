"""F27: logo + QR on the receipt, cashier, item count and the change due.
The logo is cleaned on upload and stored apart from the receipt settings (the audit keeps only its hash);
the change due is printed from what the cashier typed and never stored; a bad value is refused
before the sale is saved."""
import base64
import io
import os
import tempfile
from unittest import mock

from PIL import Image, ImageDraw

from app.core.errors import ValidationFailed
from app.printing import escpos, logo, qr, settings as rs
from app.printing import service as printing
from tests.helpers import DBTestCase
from tests.test_api import H
from tests.test_checkout_devices import CheckoutCase


def png_data_url(size=(300, 120), transparent=False) -> str:
    img = Image.new("RGBA" if transparent else "RGB", (size[0] + 40, size[1] + 40),
                    (0, 0, 0, 0) if transparent else (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 20 + size[0], 20 + size[1]], outline=(0, 0, 0, 255), width=8)
    d.line([20, 20, 20 + size[0], 20 + size[1]], fill=(0, 0, 0, 255), width=8)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


SALE = {"sale_no": 9, "subtotal": 3000, "discount_total": 0, "total": 3000, "paid_cash": 3000,
        "lines": [{"item": {"name": "Brufen", "base_unit": "tab"}, "qty_base": 20, "line_total": 1500},
                  {"item": {"name": "Panadol", "base_unit": "tab"}, "qty_base": 24, "line_total": 1500}]}


class TestSettings(DBTestCase):
    def test_logo_is_cleaned_stored_apart_and_removable(self):
        self.assertFalse(rs.get(self.db, self.ctx.org_id)["has_logo"])
        out = rs.save(self.db, self.ctx, {"logo": png_data_url(transparent=True)})
        self.assertTrue(out["has_logo"])
        png = logo.get_png(self.db, self.ctx.org_id)
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.mode, "L")
        self.assertLessEqual(img.width, logo.STORE_W)
        self.assertEqual(ImageOps_dark(img), (0, 0, img.width, img.height))   # the white margin is trimmed
        stored = self.db.one("SELECT value FROM org_settings WHERE org_id=? AND key='receipt'", (self.ctx.org_id,))
        self.assertNotIn("logo", stored["value"])
        a = self.db.one("SELECT after_json FROM audit_log WHERE action='settings.receipt_logo'")
        self.assertIn("sha256", a["after_json"])
        self.assertLess(len(a["after_json"]), 200)                    # the audit never holds the image
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='settings.receipt'")[0], 0)
        self.assertFalse(rs.save(self.db, self.ctx, {"logo": ""})["has_logo"])
        self.assertIsNone(logo.get_png(self.db, self.ctx.org_id))

    def test_bad_logo_and_long_qr_are_refused(self):
        for bad in ("data:image/png;base64,@@@", "data:image/png,abc", base64.b64encode(b"not an image").decode(),
                    "data:image/png;base64," + base64.b64encode(b"x" * (logo.MAX_UPLOAD + 1)).decode()):
            with self.assertRaises(ValidationFailed):
                rs.save(self.db, self.ctx, {"logo": bad})
        blank = Image.new("RGB", (50, 50), "white")
        buf = io.BytesIO()
        blank.save(buf, "PNG")
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"logo": base64.b64encode(buf.getvalue()).decode()})
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"qr_text": "x" * 214})
        c = rs.save(self.db, self.ctx, {"qr_text": "  https://wa.me/201000000000 ", "show_item_count": False})
        self.assertEqual((c["qr_text"], c["show_item_count"], c["show_change"]), ("https://wa.me/201000000000", False, True))


class TestDirectReceipt(DBTestCase):
    def test_lines_show_cashier_count_and_change_only_when_due(self):
        cfg = rs.get(self.db, self.ctx.org_id)
        lines = escpos.receipt_lines("ص", SALE, "w", cfg, seller="منى", given=5000)
        self.assertIn(("small", "البائع: منى", ""), lines)
        self.assertIn(("small", "عدد الأصناف: 2", ""), lines)
        self.assertIn(("row", "المدفوع من العميل", "50.00"), lines)
        self.assertIn(("total", "الباقي", "20.00"), lines)
        for given in (0, 3000, 2000):                                   # nothing handed over beyond the cash part
            self.assertFalse([x for x in escpos.receipt_lines("ص", SALE, "w", cfg, given=given) if x[1] == "الباقي"])
        off = {**cfg, "show_change": False, "show_item_count": False, "show_cashier": False}
        lines = escpos.receipt_lines("ص", SALE, "w", off, seller="منى", given=5000)
        self.assertFalse([x for x in lines if x[1] in ("الباقي", "عدد الأصناف: 2", "البائع: منى")])
        card = {**SALE, "paid_cash": 0, "paid_card": 3000}
        self.assertFalse([x for x in escpos.receipt_lines("ص", card, "w", cfg, given=5000) if x[1] == "الباقي"])
        disc = {**SALE, "subtotal": 3500, "discount_total": 500}
        self.assertIn(("row", "الإجمالي قبل الخصم", "35.00"), escpos.receipt_lines("ص", disc, "w", cfg))

    def test_header_layouts(self):
        png = logo.clean(png_data_url())
        self.assertIsNone(escpos.receipt_header(576))
        for width in (escpos.DOTS_80MM, escpos.DOTS_58MM):
            both = escpos.receipt_header(width, png, "https://wa.me/201000000000")
            self.assertEqual(both.width, width)
            left, right = both.crop((0, 0, width // 2, both.height)), both.crop((width // 2, 0, width, both.height))
            self.assertIsNotNone(ImageOps_dark(left))                   # QR on the left
            self.assertIsNotNone(ImageOps_dark(right))                  # logo on the right
            only_qr = escpos.receipt_header(width, None, "https://wa.me/201000000000")
            box = ImageOps_dark(only_qr)
            self.assertLess(abs((box[0] + box[2]) / 2 - width / 2), 4)  # centred

    def test_printed_bytes_carry_the_logo_and_a_readable_qr(self):
        rs.save(self.db, self.ctx, {"logo": png_data_url(), "qr_text": "https://wa.me/201000000000"})
        cfg = rs.get(self.db, self.ctx.org_id)
        if not escpos.shaping_available():
            self.skipTest("The thermal receipt requires libraqm")
        img = printing.receipt_image("صيدلية تجربة", SALE, "w", cfg, logo_png=logo.get_png(self.db, self.ctx.org_id))
        job = escpos.raster_bytes(img)
        wb, h = job[4] | job[5] << 8, job[6] | job[7] << 8
        bits = job[8:]
        self.assertEqual(len(bits), wb * h)
        printed = Image.frombytes("1", (wb * 8, h), bytes(b ^ 0xFF for b in bits)).convert("L")
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed (optional cross-check)")
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(np.array(printed))[0], "https://wa.me/201000000000")

    def test_broken_logo_never_stops_the_receipt(self):
        rs.save(self.db, self.ctx, {"qr_text": "https://wa.me/201000000000"})
        cfg = {**rs.get(self.db, self.ctx.org_id), "has_logo": True}
        if not escpos.shaping_available():
            self.skipTest("The thermal receipt requires libraqm")
        expected = printing.receipt_image("صيدلية تجربة", SALE, "w", cfg)
        with self.assertLogs(printing.log, "WARNING"):
            actual = printing.receipt_image("صيدلية تجربة", SALE, "w", cfg, logo_png=b"broken")
        self.assertEqual(actual.tobytes(), expected.tobytes())


def ImageOps_dark(img):  # noqa: N802 — bounding box of the dark pixels
    from PIL import ImageOps
    return ImageOps.invert(img.convert("L")).point(lambda p: 255 if p > 128 else 0).getbbox()


class TestCheckoutChange(CheckoutCase):
    def test_cash_given_reaches_the_printed_receipt_only(self):
        seen = []
        real = escpos.receipt_lines
        with mock.patch.object(escpos, "receipt_lines", side_effect=lambda *a, **k: seen.append(k) or real(*a, **k)):
            self.complete(self.draft(), [{"method": "cash", "amount": 3000}], receipt=True, cash_given=5000)
        self.assertEqual(seen[0]["given"], 5000)
        cols = [r[1] for r in self.db.all("PRAGMA table_info(sales)")]
        self.assertFalse([c for c in cols if "given" in c or "tender" in c])     # nothing new is stored

    def test_bad_cash_given_is_refused_before_the_sale_is_saved(self):
        for bad in (-1, "abc", 1.5, 100_000_001):
            sid = self.draft()
            f = self.c.post(f"/api/sales/{sid}/check", json={}, headers=H).json()["findings"]
            r = self.c.post(f"/api/sales/{sid}/complete", headers=H, json={
                "payments": [{"method": "cash", "amount": 3000}], "request_id": f"bad{bad}", "cash_given": bad,
                "safety_decisions": {str(i): "x" for i in range(len(f))}})
            self.assertEqual(r.status_code, 422, bad)
            self.assertEqual(self.db.one("SELECT status FROM sales WHERE id=?", (sid,))[0], "draft")

    def test_browser_receipt_shows_logo_qr_count_and_change(self):
        self.c.put("/api/settings/receipt", headers=H, json={"logo": png_data_url(), "qr_text": "https://wa.me/201000000000"})
        done = self.complete(self.draft(), [{"method": "cash", "amount": 3000}])
        page = self.c.get(f"/receipt/{done['id']}?given=5000").text
        self.assertIn('src="data:image/png;base64,', page)
        self.assertIn("<svg", page)
        self.assertIn("عدد الأصناف", page)
        self.assertIn("الباقي", page)
        self.assertIn("20.00", page)
        plain = self.c.get(f"/receipt/{done['id']}?given=junk").text                  # a reprint: no change line
        self.assertNotIn("الباقي", plain)
        r = self.c.get("/api/settings/receipt/logo")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
        self.c.put("/api/settings/receipt", headers=H, json={"logo": ""})
        self.assertEqual(self.c.get("/api/settings/receipt/logo").status_code, 404)


class TestQRLinks(DBTestCase):
    def test_mobile_numbers_become_whatsapp_links_and_links_stay(self):
        from app.printing import qrlinks
        for typed in ("01012345678", " 010 1234 5678 ", "+201012345678", "00201012345678", "201012345678", "1012345678"):
            self.assertEqual(qrlinks.normalize(typed), "https://wa.me/201012345678", typed)
        for kept in ("https://www.facebook.com/x", "0482221234", "01312345678", "https://maps.app.goo.gl/abc", ""):
            self.assertEqual(qrlinks.normalize(kept), kept.strip())
        cases = {"https://wa.me/201012345678": "اطلب على واتساب", "https://maps.app.goo.gl/abc": "موقعنا على الخريطة",
                 "https://www.google.com/maps/place/x": "موقعنا على الخريطة", "https://www.google.com/search?q=x": "",
                 "https://www.facebook.com/x": "تابعنا على فيسبوك", "https://ipn.eg/S/x": "ادفع بإنستاباي",
                 "https://example.com": "", "not a link": ""}
        for link, cap in cases.items():
            self.assertEqual(qrlinks.auto_caption(link), cap, link)
        self.assertEqual(qrlinks.caption("https://wa.me/201012345678", " اطلب دواك "), "اطلب دواك")

    def test_settings_normalize_both_codes(self):
        c = rs.save(self.db, self.ctx, {"qr_text": "0101 234 5678", "qr2_text": " https://maps.app.goo.gl/abc ",
                                        "qr2_caption": " " + "ك" * 50})
        self.assertEqual((c["qr_text"], c["qr2_text"]), ("https://wa.me/201012345678", "https://maps.app.goo.gl/abc"))
        self.assertEqual(len(c["qr2_caption"]), 40)
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"qr2_text": "x" * 214})

    def test_captioned_codes_print_and_read(self):
        bare = escpos.qr_block(576, "https://wa.me/201012345678")
        cap = escpos.qr_block(576, "https://wa.me/201012345678", "اطلب على واتساب")
        self.assertGreater(cap.height, bare.height)
        self.assertIsNotNone(ImageOps_dark(cap.crop((0, bare.height, cap.width, cap.height))))   # the caption is drawn
        self.assertIsNone(escpos.receipt_footer_qr(576, ""))
        foot = escpos.receipt_footer_qr(576, "https://maps.app.goo.gl/abc", "موقعنا على الخريطة")
        self.assertEqual(foot.width, 576)
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed (optional cross-check)")
        printed = Image.frombytes("1", (576, foot.height), bytes(b ^ 0xFF for b in escpos.raster_bytes(foot)[8:])).convert("L")
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(np.array(printed))[0], "https://maps.app.goo.gl/abc")

    def test_logo_preview_is_exactly_black_and_white(self):
        # teal text on a navy box: both are dark, so both print black — the preview must show that
        img = Image.new("RGB", (200, 80), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 190, 70], fill=(49, 42, 86))
        d.rectangle([40, 30, 160, 50], fill=(30, 140, 141))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        look = Image.open(io.BytesIO(logo.print_look(logo.clean(base64.b64encode(buf.getvalue()).decode()))))
        self.assertEqual(look.convert("L").getextrema(), (0, 0))   # one black block: the teal text is gone on paper


class TestBrowserTwoCodes(CheckoutCase):
    def test_page_has_both_codes_with_captions(self):
        self.c.put("/api/settings/receipt", headers=H, json={"qr_text": "01012345678", "qr2_text": "https://maps.app.goo.gl/abc"})
        done = self.complete(self.draft(), [{"method": "cash", "amount": 3000}])
        page = self.c.get(f"/receipt/{done['id']}").text
        self.assertEqual(page.count("<svg"), 2)
        self.assertIn("<figcaption>اطلب على واتساب</figcaption>", page)
        self.assertIn("<figcaption>موقعنا على الخريطة</figcaption>", page)


class TestLongCaption(DBTestCase):
    def test_long_caption_never_pushes_the_code_off_the_paper(self):
        # Codex P2 on #103: 40 wide letters used to widen the QR block past the receipt
        png = logo.clean(png_data_url())
        long = "W" * 40
        for width in (escpos.DOTS_80MM, escpos.DOTS_58MM):
            block = escpos.qr_block(width, "https://wa.me/201012345678", long, max_w=width // 2 - 8)
            self.assertLessEqual(block.width, width // 2 - 8)
            hdr = escpos.receipt_header(width, png, "https://wa.me/201012345678", long)
            self.assertEqual(hdr.width, width)
            self.assertIsNotNone(ImageOps_dark(hdr.crop((width // 2, 0, width, hdr.height))))   # the logo still has room
            foot = escpos.receipt_footer_qr(width, "https://wa.me/201012345678", long)
            self.assertEqual(foot.width, width)
            try:
                import cv2
                import numpy as np
            except ImportError:
                continue
            for img in (hdr, foot):
                printed = Image.frombytes("1", (width, img.height),
                                          bytes(b ^ 0xFF for b in escpos.raster_bytes(img)[8:])).convert("L")
                self.assertEqual(cv2.QRCodeDetector().detectAndDecode(np.array(printed))[0], "https://wa.me/201012345678")
