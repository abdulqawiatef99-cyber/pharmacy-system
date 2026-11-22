"""U2-23 / U2-24 receipt: the sold unit (one box, not «6 strips»), the unit price, each line's discount, the customer
with phone and address on a credit sale, each payment method, his balance, the total quantity and the return line."""
from app.db import new_id
from app.printing import escpos
from tests.test_sales import SalesCase


def texts(lines):
    return "\n".join(f"{a} {b}" for _, a, b in lines)


class TestReceipt(SalesCase):
    def test_sold_unit_and_price(self):
        l = {"item": {"pack_size": 6, "pack_unit": "pack", "base_unit": "شريط"}, "qty_base": 6, "gross": 29100}
        self.assertEqual((escpos.qty_text(l), escpos.unit_price(l)), ("1 علبة", 29100))
        l = {**l, "qty_base": 8, "gross": 38800}
        self.assertEqual(escpos.qty_text(l), "1 علبة + 2 شريط")
        self.assertEqual(escpos.unit_price(l), 4850)

    def test_credit_receipt(self):
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, phone, credit_limit, notes) VALUES (?,?,?,?,?,?)",
                            (cid, self.ctx.org_id, "عميل تجربة", "01000000000", 1000000, "كود إي ستوك: 77"))
        sid = self.s.create(self.ctx, cid)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=1000)    # one box of 2 strips
        d = self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        t = texts(escpos.receipt_lines("صيدلية تجربة", d, "اليوم", {"show_customer": True}, return_days=14))
        self.assertIn(f"الرصيد السابق {0:.2f}", t)
        self.assertIn(f"الرصيد الحالي {d['total'] / 100:.2f}", t)
        for part in ("كود 77", "العميل: عميل تجربة", "موبايل: 01000000000", "1 علبة ×", "خصم 10%", "آجل", "الرصيد السابق",
                     "الرصيد الحالي", "إجمالي الكمية: 1 علبة", "المرتجع خلال 14 يومًا"):
            self.assertIn(part, t)


from tests.test_owner_plan_priority_1_2 import PlanCase          # noqa: E402


class TestBrowserReceipt(PlanCase):
    def test_page_shows_unit_price_and_total_qty(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        page = self.c.get(f"/receipt/{done['id']}").text
        self.assertIn("1 علبة ×", page)
        self.assertIn("إجمالي الكمية", page)


class TestReprintBalance(TestReceipt):
    def test_no_balances_after_his_account_moved(self):
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,?)",
                            (cid, self.ctx.org_id, "عميل", 1000000))
        sid = self.s.create(self.ctx, cid)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        self.s.customer_payment(self.ctx, cid, 100)
        t = texts(escpos.receipt_lines("ص", self.s.get(self.ctx, sid), "اليوم", {"show_customer": True}))
        self.assertNotIn("الرصيد السابق", t)
