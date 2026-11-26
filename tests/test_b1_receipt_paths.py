"""B1 (owner 22/11 19:44, live on the cashier PC): the receipt printed by the direct printer right after a sale had no
customer details, while the reprint from «فواتير البيع» (the browser page) had them. Both paths must print the same
content: the customer (and code), his phone and address on a credit or delivery sale, the delivery details, each
payment. Synthetic data only."""
from app.db import new_id
from app.printing import escpos
from app.sales.service import Sales
from tests.test_owner_plan_priority_1_2 import PlanCase


def texts(lines):
    return "\n".join(f"{a} {b}" for _, a, b in lines)


class TestBothPathsSameCustomer(PlanCase):
    def customer(self):
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, phone, address1, credit_limit, notes)"
                            " VALUES (?,?,?,?,?,?,?)", (cid, self.ctx.org_id, "عميل الإيصال", "01011112222",
                                                        "شارع التجربة 5", 10000000, "كود إي ستوك: 91"))
        return cid

    def both(self, sale_id):
        page = self.c.get(f"/receipt/{sale_id}").text
        from app.sales.service import return_window_days
        direct = texts(escpos.receipt_lines("صيدلية تجربة", Sales(self.db).get(self.ctx, sale_id), "اليوم",
                                            {"show_customer": True}, return_days=return_window_days(self.db, self.ctx.org_id)))
        return page, direct

    def test_credit_sale_customer_on_both(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft(self.customer())
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "credit", "amount": d["total"]}])
        page, direct = self.both(done["id"])
        for part in ("عميل الإيصال", "كود 91", "01011112222", "شارع التجربة 5"):
            self.assertIn(part, page, part)
            self.assertIn(part, direct, part)

    def test_delivery_details_on_both(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft(self.customer())
        self.call("PATCH", f"/api/sales/{sid}", json={"kind": "delivery", "delivery": {"address": "عنوان التوصيل 7",
                                                                                      "phone": "01033334444"}})
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        page, direct = self.both(done["id"])
        for part in ("عميل الإيصال", "عنوان التوصيل 7", "01033334444"):
            self.assertIn(part, page, part)
            self.assertIn(part, direct, part)

    def test_cash_sale_customer_name_on_both(self):
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft(self.customer())
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        page, direct = self.both(done["id"])
        self.assertIn("عميل الإيصال", page)
        self.assertIn("عميل الإيصال", direct)

    def test_no_return_star_policy_and_subtotal_on_both(self):
        # agent review P2s on #423: the ✱ note, the policy line and «الإجمالي» were browser-only
        cat = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO categories (id, org_id, name, no_return) VALUES (?,?,?,1)",
                            (cat, self.ctx.org_id, "ثلاجة تجربة"))
            self.db.execute("UPDATE items SET category_id=? WHERE id=?", (cat, self.box))
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.box, packs=1)
        done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        page, direct = self.both(done["id"])
        for part in ("✱", "لا تُقبل إعادتها", "المرتجع خلال", "الإجمالي"):
            self.assertIn(part, page, part)
            self.assertIn(part, direct, part)

    def test_policy_text_zero_days(self):
        self.assertEqual(escpos.policy_text(0, False), "المرتجع غير متاح بهذه الفاتورة")
        self.assertEqual(escpos.policy_text(14, False), "المرتجع خلال 14 يومًا بهذه الفاتورة")
        self.assertEqual(escpos.policy_text(7, False), "المرتجع خلال 7 أيام بهذه الفاتورة")


class TestCodexRound2(__import__("tests.test_sales", fromlist=["SalesCase"]).SalesCase):
    """Codex on #423: no averaged unit price for a line from batches at different prices; a long policy row is
    wrapped on thermal paper, never cut."""
    def test_two_batch_prices_print_each_official_price(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=6)      # 3 units from OLD @38/2, 3 from NEW @40/2
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        sale = self.s.get(self.ctx, sid)
        line = sale["lines"][0]
        self.assertEqual(len({a["pack_price"] for a in line["allocations"]}), 2)
        self.assertIsNone(escpos.unit_price(line))
        rows = escpos.receipt_lines("صيدلية", sale, "اليوم", {})
        price_cells = {cells[1:3] for kind, _, cells in rows if kind == "item"}
        self.assertEqual(price_cells, {("1 علبة", "38.00"), ("1 علبة", "40.00"),
                                       ("1 وحدة", "19.00"), ("1 وحدة", "20.00")})

    def test_one_batch_price_keeps_the_unit_price(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)      # one box from OLD @38
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        line = self.s.get(self.ctx, sid)["lines"][0]
        self.assertEqual(escpos.unit_price(line), 3800)

    def test_long_policy_row_is_wrapped_not_cut(self):
        if not escpos.shaping_available():
            self.skipTest("raqm not available")
        long = escpos.policy_text(14, True) + " " + "كلمة " * 30
        rows = [("small", long, "")]
        one = escpos.render_receipt(rows)
        self.assertGreater(one.height, 28 * 3)                     # several rows, nothing cut with «…»
