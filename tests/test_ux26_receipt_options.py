"""UX-26 PR3 (owner 26/11): receipt options. One name (whoever made the sale), item / invoice discount lines
that can be hidden (then the lines show the net and the receipt still adds up), the rounding line hidden by
default (the total printed is the final amount), and the customer's name, phone and address bold and larger.
Both receipts (direct thermal and browser page) follow the same settings. Synthetic data only."""
import re

from app.printing import escpos, settings as rs
from app.sales.service import Sales
from tests.test_owner_plan_priority_1_2 import PlanCase

SALE = {
    "sale_no": 7, "kind": "store", "subtotal": 15000, "discount_total": 1500, "total": 13500, "rounding": 0,
    "paid_cash": 13500,
    "lines": [
        # 2 boxes at 50.00, item discount 10% = 10.00, invoice share 3.00
        {"item": {"name": "Alpha", "name_en": "Alpha", "pack_size": 1}, "qty_base": 2, "line_total": 8700,
         "discount_bp": 1000, "discount": 1300, "invoice_share": 300, "gross": 10000,
         "official_prices": [{"pack": True, "qty": 2, "price": 5000, "gross": 10000}]},
        # 1 box at 30.00 + 1 box at 20.00 (two public prices), invoice share 2.00
        {"item": {"name": "Beta", "name_en": "Beta", "pack_size": 1}, "qty_base": 2, "line_total": 4800,
         "discount_bp": 0, "discount": 200, "invoice_share": 200, "gross": 5000,
         "official_prices": [{"pack": True, "qty": 1, "price": 3000, "gross": 3000},
                             {"pack": True, "qty": 1, "price": 2000, "gross": 2000}]},
    ],
}


def rows(cfg, sale=SALE, **kw):
    return escpos.receipt_lines("صيدلية", sale, "اليوم", {**rs.DEFAULTS, **cfg}, **kw)


def money_rows(out):
    return {r[1]: r[2] for r in out if r[0] in ("row", "total")}


def item_amounts(out):
    return [r[2][3] for r in out if r[0] == "item"]


class TestThermalOptions(PlanCase):
    def test_defaults_keep_todays_discount_lines(self):
        out = rows({})
        m = money_rows(out)
        self.assertEqual(m["الإجمالي قبل الخصم"], "150.00")
        self.assertEqual(m["خصم الأصناف"], "10.00")
        self.assertEqual(m["خصم الفاتورة"], "5.00")
        self.assertEqual(m["المطلوب"], "135.00")
        self.assertEqual(item_amounts(out), ["100.00", "30.00", "20.00"])
        self.assertTrue(any(r[0] == "detail" and r[1].startswith("خصم") for r in out))

    def test_hidden_item_discount_shows_net_lines_and_adds_up(self):
        out = rows({"show_item_discount": False})
        m = money_rows(out)
        self.assertNotIn("خصم الأصناف", m)
        self.assertEqual(m["خصم الفاتورة"], "5.00")
        self.assertEqual(m["الإجمالي قبل الخصم"], "140.00")
        self.assertEqual(item_amounts(out), ["90.00", "30.00", "20.00"])
        self.assertIn(("1", "2 علبة", "45.00", "90.00"), [r[2] for r in out if r[0] == "item"])   # net price
        self.assertFalse(any(r[0] == "detail" and r[1].startswith("خصم") for r in out))
        self.assertEqual(m["المطلوب"], "135.00")
        self.assertIn(("center", "وفّرت: 5.00 ج", ""), out)

    def test_both_hidden_prints_nets_only(self):
        out = rows({"show_item_discount": False, "show_invoice_discount": False})
        m = money_rows(out)
        self.assertNotIn("خصم الأصناف", m)
        self.assertNotIn("خصم الفاتورة", m)
        self.assertEqual(m["الإجمالي قبل الخصم"], "135.00")
        # 3.00 + 10.00 off Alpha, 2.00 spread on Beta's two prices by their share (1.20 / 0.80)
        self.assertEqual(item_amounts(out), ["87.00", "28.80", "19.20"])
        self.assertEqual(sum(int(a.replace(".", "")) for a in item_amounts(out)), 13500)
        self.assertFalse(any("وفّرت" in r[1] for r in out))
        self.assertEqual(m["المطلوب"], "135.00")

    def test_spread_never_loses_a_piaster(self):
        sale = {**SALE, "discount_total": 100, "total": 4900, "subtotal": 5000, "lines": [
            {**SALE["lines"][1], "discount": 100, "invoice_share": 100, "line_total": 4900,
             "official_prices": [{"pack": True, "qty": 1, "price": 1000, "gross": 1000},
                                 {"pack": True, "qty": 1, "price": 1000, "gross": 1000},
                                 {"pack": True, "qty": 1, "price": 3000, "gross": 3000}]}]}
        out = rows({"show_invoice_discount": False}, sale)
        cents = [int(a.replace(".", "")) for a in item_amounts(out)]
        self.assertEqual(sum(cents), 4900)
        self.assertTrue(all(c >= 0 for c in cents))

    def test_rounding_hidden_by_default_total_is_final(self):
        sale = {**SALE, "rounding": -25}
        m = money_rows(rows({}, sale))
        self.assertNotIn("التقريب", m)
        self.assertEqual(m["المطلوب"], "134.75")
        m = money_rows(rows({"show_rounding": True}, sale))
        self.assertEqual(m["التقريب"], "-0.25")
        self.assertEqual(m["المطلوب"], "134.75")

    def test_one_name_only(self):
        out = rows({}, seller="منى", cashier="أحمد")
        names = [r[1] for r in out if r[1].startswith(("البائع", "الكاشير"))]
        self.assertEqual(names, ["البائع: منى"])
        out = rows({}, seller="", cashier="أحمد")
        self.assertEqual([r[1] for r in out if r[1].startswith(("البائع", "الكاشير"))], ["البائع: أحمد"])
        out = rows({"show_cashier": False}, seller="منى", cashier="أحمد")
        self.assertEqual([r[1] for r in out if r[1].startswith(("البائع", "الكاشير"))], [])

    def test_customer_lines_bold_and_larger(self):
        sale = {**SALE, "on_credit": 13500, "paid_cash": 0,
                "customer": {"name": "عميل تجريبي", "phone": "01000000000", "address1": "شارع 1"}}
        out = rows({}, sale)
        cust = [r for r in out if r[1].startswith(("العميل", "موبايل", "العنوان"))]
        self.assertEqual(len(cust), 3)
        self.assertEqual({r[0] for r in cust}, {"customer"})
        if escpos.shaping_available():
            escpos.render_receipt(out)          # the new row kind renders

    def test_settings_save_the_new_toggles(self):
        self.assertTrue(rs.DEFAULTS["show_item_discount"])
        self.assertTrue(rs.DEFAULTS["show_invoice_discount"])
        self.assertFalse(rs.DEFAULTS["show_rounding"])
        rs.save(self.db, self.ctx, {"show_item_discount": False, "show_invoice_discount": False, "show_rounding": True})
        got = rs.get(self.db, self.ctx.org_id)
        self.assertEqual((got["show_item_discount"], got["show_invoice_discount"], got["show_rounding"]), (False, False, True))


class TestBrowserReceiptOptions(PlanCase):
    def sold(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        self.line(sid, self.box, packs=1, discount_bp=1000)                  # 100.00 - 10.00
        Sales(self.db).set_invoice_discount(self.ctx, sid, amount=500)       # - 5.00
        d = self.call("GET", f"/api/sales/{sid}")
        return self.complete(sid, [{"method": "cash", "amount": d["total"]}])

    def page(self, sale_id):
        return self.c.get(f"/receipt/{sale_id}").text

    def test_default_and_hidden_discounts(self):
        sid = self.sold()["id"]
        page = self.page(sid)
        self.assertRegex(page, r"الخصم</td><td>15\.00<")
        self.assertIn("خصم 10%", page)
        rs.save(self.db, self.ctx, {"show_item_discount": False, "show_invoice_discount": False})
        page = self.page(sid)
        self.assertNotIn("الخصم</td>", page)
        self.assertNotIn("خصم 10%", page)
        self.assertRegex(page, r"الإجمالي</td><td>85\.00<")
        self.assertRegex(page, r"المطلوب</td><td>85\.00<")
        self.assertIn("1 علبة × 85.00", page)

    def test_rounding_hidden_by_default(self):
        sid = self.sold()["id"]
        with self.db.transaction():
            self.db.execute("UPDATE sales SET rounding=-50 WHERE id=?", (sid,))
        page = self.page(sid)
        self.assertNotIn("التقريب", page)
        self.assertRegex(page, r"المطلوب</td><td>84\.50<")
        rs.save(self.db, self.ctx, {"show_rounding": True})
        self.assertIn("التقريب", self.page(sid))

    def test_customer_bold(self):
        from app.db import new_id
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, phone, address1, credit_limit) VALUES (?,?,?,?,?,?)",
                            (cid, self.ctx.org_id, "عميل الإيصال", "01011112222", "شارع التجربة 5", 10000000))
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft(cid)
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "credit", "amount": d["total"]}])
        page = self.page(done["id"])
        for part in ("العميل: عميل الإيصال", "موبايل: 01011112222", "العنوان: شارع التجربة 5"):
            self.assertRegex(page, rf'<b class="cu">{re.escape(part)}')
