"""UX-26 (d), owner 26/11: an English item name prints left to right on the Arabic receipt (browser page and
direct thermal printer), and the cash refund label says the money leaves the drawer. Synthetic data only."""
from unittest import mock

from app.printing import escpos
from tests.test_owner_plan_priority_1_2 import PlanCase


class TestEnglishNameDirection(PlanCase):
    def sold(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.box, packs=1)
        return self.complete(sid, [{"method": "cash", "amount": d["total"]}])

    def test_browser_receipt_isolates_the_name(self):
        page = self.c.get(f"/receipt/{self.sold()['id']}").text
        self.assertIn("<bdi>TRIAL BOX 3 STRIPS</bdi>", page)

    def test_direction_of_a_name(self):
        self.assertEqual(escpos.text_direction("PANADOL EXTRA (20 TAB)"), "ltr")
        self.assertEqual(escpos.text_direction("12 PANADOL"), "ltr")
        self.assertEqual(escpos.text_direction("بانادول اكسترا"), "rtl")
        self.assertEqual(escpos.text_direction("—"), "rtl")
        self.assertEqual(escpos.text_direction("PANADOL / بانادول"), "ltr")

    def test_thermal_item_name_drawn_left_to_right(self):
        if not escpos.shaping_available():
            self.skipTest("raqm not available")
        drawn = []
        real = escpos.ImageDraw.ImageDraw.text

        def spy(self_, xy, text, *a, **kw):
            drawn.append((text, kw.get("direction")))
            return real(self_, xy, text, *a, **kw)
        with mock.patch.object(escpos.ImageDraw.ImageDraw, "text", spy):
            escpos.render_receipt([("item", "PANADOL (20)", ("1", "1 علبة", "10.00", "10.00")),
                                   ("item", "بانادول", ("2", "1 علبة", "10.00", "10.00"))])
        self.assertIn(("PANADOL (20)", "ltr"), drawn)
        self.assertIn(("بانادول", "rtl"), drawn)


class TestEnglishCapitalsOnReceipt(PlanCase):
    def test_both_receipts_print_english_in_capitals(self):
        from app.sales.service import Sales
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        page = self.c.get(f"/receipt/{done['id']}").text
        self.assertIn("<bdi>TRIAL BOX 3 STRIPS</bdi>", page)
        rows = escpos.receipt_lines("صيدلية", Sales(self.db).get(self.ctx, done["id"]), "اليوم", {})
        self.assertIn("TRIAL BOX 3 STRIPS", [r[1] for r in rows if r[0] == "item"])
        stored = self.db.one("SELECT name_en FROM items WHERE id=?", (self.box,))["name_en"]
        self.assertEqual(stored, "Trial Box 3 strips")          # stored data is not rewritten

    def test_upper_en_leaves_arabic_and_micro_sign(self):
        self.assertEqual(escpos.upper_en("vit d 5µg بانادول"), "VIT D 5µG بانادول")
