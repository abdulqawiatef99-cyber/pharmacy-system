"""The guards between the cashier's preview and the sale's write, and around a return, that no test had walked.

- Another cashier edits the same open invoice, or the price changes, while it is being completed: refused,
  nothing written, the invoice stays open.
- A return names a line of another invoice, a quantity that is not whole, a refund on account without a
  customer, a draft invoice, or no reason: refused, nothing moves.
Synthetic data only.
"""
from app.catalog import service as cat
from app.sales.service import Sales
from tests.test_owner_plan_priority_1_2 import PlanCase


class CompletionGuards(PlanCase):
    def setUp(self):
        super().setUp()
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})

    def untouched(self, sid, stock):
        s = self.call("GET", f"/api/sales/{sid}")
        self.assertEqual(s["status"], "draft")
        self.assertEqual(self.qty(self.pan), stock)
        self.assertEqual(self.drawer(), 10000)
        self.assertEqual(self.db.one("SELECT count(*) n FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id"
                                     " JOIN sales s ON s.id=l.sale_id WHERE s.org_id=? AND l.sale_id=?",
                                     (self.ctx.org_id, sid))["n"], 0)

    def complete_with(self, sid, meanwhile):
        """Complete `sid` while `meanwhile()` runs after the preview's checks and before the write."""
        sales = Sales(self.db)
        real = sales.safety_check

        def hook(ctx, s):
            out = real(ctx, s)
            meanwhile()
            return out
        sales.safety_check = hook
        d = self.call("GET", f"/api/sales/{sid}")
        with self.assertRaises(Exception) as e:
            sales.complete(self.ctx, sid, payments=[{"method": "cash", "amount": d["total"]}], safety_decisions={},
                           version=d["version"])
        return getattr(e.exception, "code", None)

    def test_an_edit_by_another_cashier_meanwhile_is_refused(self):
        sid = self.draft()
        self.line(sid, self.pan, units=2)
        stock = self.qty(self.pan)
        code = self.complete_with(sid, lambda: Sales(self.db).add_line(self.ctx, sid, self.box, units=1))
        self.assertEqual(code, "stale_version")
        self.untouched(sid, stock)

    def test_a_price_change_meanwhile_is_refused(self):
        sid = self.draft()
        self.line(sid, self.pan, units=2)                                  # 40.00 on the screen
        stock = self.qty(self.pan)
        code = self.complete_with(sid, lambda: cat.update_item(self.db, self.ctx, self.pan, {"public_price": 5000}))
        self.assertEqual(code, "price_changed")
        self.untouched(sid, stock)
        d = self.call("GET", f"/api/sales/{sid}")                          # the screen shows the new price
        done = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        self.assertEqual(done["total"], 5000)

    def test_an_empty_invoice_is_refused(self):
        sid = self.draft()
        r = self.complete(sid, [{"method": "cash", "amount": 0}], expected=422)
        self.assertIn("فارغة", r["error"]["message"])


class ReturnGuards(PlanCase):
    def setUp(self):
        super().setUp()
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        self.sale = self.sell(self.pan, units=2)
        self.lid = self.sale["lines"][0]["id"]

    def ret(self, sid, lines, expected, method="cash", reason="مرتجع تجربة"):
        return self.call("POST", f"/api/sales/{sid}/return", expected,
                         json={"lines": lines, "refund_method": method, "reason": reason})

    def snapshot(self):
        return (self.call("GET", f"/api/sales/{self.sale['id']}")["returned_total"], self.qty(self.pan),
                self.qty(self.box), self.drawer(),
                self.db.one("SELECT count(*) n FROM stock_movements WHERE org_id=? AND movement_type='sale_return'",
                            (self.ctx.org_id,))["n"])

    def test_bad_returns_are_refused_and_move_nothing(self):
        other = self.sell(self.box, units=1)
        draft = self.draft()
        self.line(draft, self.pan, units=1)
        before = self.snapshot()
        self.assertEqual(before[0], 0)
        cases = [
            (self.sale["id"], {other["lines"][0]["id"]: 1}, 404, "cash", "مرتجع تجربة"),   # a line of another invoice
            (self.sale["id"], {self.lid: 1.5}, 422, "cash", "مرتجع تجربة"),
            (self.sale["id"], {self.lid: 0}, 422, "cash", "مرتجع تجربة"),
            (self.sale["id"], {self.lid: True}, 422, "cash", "مرتجع تجربة"),
            (self.sale["id"], {self.lid: 1}, 422, "credit", "مرتجع تجربة"),              # no customer on the sale
            (self.sale["id"], {self.lid: 1}, 422, "cheque", "مرتجع تجربة"),
            (self.sale["id"], {self.lid: 1}, 422, "cash", "  "),                          # no reason
            (draft, {self.lid: 1}, 409, "cash", "مرتجع تجربة"),                           # not completed
            ("missing", {self.lid: 1}, 404, "cash", "مرتجع تجربة"),
        ]
        for sid, lines, expected, method, reason in cases:
            with self.subTest(lines=str(lines), method=method, reason=reason, sid=sid[:8]):
                self.ret(sid, lines, expected, method, reason)
                self.assertEqual(self.snapshot(), before)

    def test_a_good_line_and_a_bad_line_together_move_nothing(self):
        other = self.sell(self.box, units=1)
        before = self.snapshot()
        self.ret(self.sale["id"], {self.lid: 1, other["lines"][0]["id"]: 1}, 404)
        self.assertEqual(self.snapshot(), before)
