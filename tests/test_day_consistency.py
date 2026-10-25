"""Review 3: one full working day through every kind of operation, then every report must tell the same story.

If two screens ever disagree about money, the owner cannot trust either, so each number below is
checked against at least one other report or against the raw ledgers."""
from datetime import date

from app.finance import expenses as fin
from app.reports import invoices, owner
from app.reports.service import sales_report
from app.sales import channels, contracts
from tests.test_cashier_acceptance import Base

D = date.today().isoformat()


class TestDay(Base):
    def setUp(self):
        super().setUp()
        channels.ensure_defaults(self.db)
        ch = {c["kind"]: c["id"] for c in channels.listing(self.db, self.ctx)}
        s, ctx, pan = self.s, self.ctx, self.pan
        self.cd.open(ctx, 10000)

        def sale(units, payments, customer=None, disc=0, delivery=None):
            sid = s.create(ctx, customer)
            s.add_line(ctx, sid, pan, units=units, discount_bp=disc)
            if delivery:
                s.set_details(ctx, sid, {"kind": "delivery", "delivery": delivery})
            f = s.safety_check(ctx, sid)
            return s.complete(ctx, sid, payments=payments, safety_decisions={i: "ok" for i in range(len(f))})

        self.s1 = sale(2, [{"method": "cash", "amount": 4000}])
        self.s2 = sale(2, [{"method": "cash", "amount": 1000}, {"method": "card", "amount": 3000, "channel_id": ch["card"]}])
        self.s3 = sale(2, [{"method": "transfer", "amount": 4000, "channel_id": ch["transfer"]}])
        self.cust = s.create_customer(ctx, name="عميل آجل", credit_limit=100000)
        self.s4 = sale(2, [{"method": "credit", "amount": 4000}], customer=self.cust)
        k = contracts.save(self.db, ctx, {"name": "نقابة", "discount_local_bp": 1000, "discount_imported_bp": 1000})[0]["id"]
        self.ccust = s.create_customer(ctx, name="عضو نقابة", credit_limit=0)
        contracts.link_customer(self.db, ctx, self.ccust, k)
        self.s5 = sale(2, [{"method": "cash", "amount": 3600}], customer=self.ccust)              # contract 10%
        self.s6 = sale(2, [{"method": "cash", "amount": 3600}], disc=1000)                       # manual 10%
        self.s7 = sale(2, [{"method": "cash", "amount": 5000}], delivery={"phone": "0100", "fee": 1000})
        s.delivery_status(ctx, self.s7["id"], "delivered")
        s.delivery_status(ctx, self.s7["id"], "settled")
        v = sale(1, [{"method": "cash", "amount": 2000}])
        s.void(ctx, v["id"], "غلط في الفاتورة")
        s.return_items(ctx, self.s1["id"], {self.s1["lines"][0]["id"]: 1}, refund_method="cash", reason="مش محتاجه")
        s.return_items(ctx, self.s4["id"], {self.s4["lines"][0]["id"]: 1}, refund_method="credit", reason="مش محتاجه")
        cat = self.db.one("SELECT id FROM expense_categories WHERE org_id=? LIMIT 1", (ctx.org_id,))
        if not cat:
            fin.ensure_defaults(self.db)
            cat = self.db.one("SELECT id FROM expense_categories WHERE org_id=? LIMIT 1", (ctx.org_id,))
        fin.record(self.db, ctx, category_id=cat["id"], amount=500, paid_from="drawer", note="أكياس")
        s.customer_payment(ctx, self.cust, 1500)
        self.expected_cash = 10000 + 4000 + 1000 + 3600 + 3600 + 5000 - 2000 - 500 + 1500
        self.summary_before_close = self.cd.summary(ctx)
        self.cd.close(ctx, counted_cash=self.expected_cash, note="")

    def test_every_report_agrees(self):
        self.assertEqual(self.summary_before_close["expected_cash"], self.expected_cash)
        inv = invoices.report(self.db, self.ctx, date_from=D, date_to=D)
        t = inv["totals"]
        self.assertEqual(t["n"], 7)                                          # the voided one is not counted
        self.assertEqual((t["subtotal"], t["item_discount"], t["customer_discount"], t["delivery_fee"]), (28000, 400, 400, 1000))
        self.assertEqual((t["total"], t["returned_total"], t["net"]), (28000 - 800 + 1000, 4000, 28000 - 800 + 1000 - 4000))
        self.assertEqual((t["paid_cash"], t["paid_card"], t["paid_transfer"], t["on_credit"]), (4000 + 1000 + 3600 + 3600 + 5000, 3000, 4000, 4000))
        self.assertEqual(t["paid_cash"] + t["paid_card"] + t["paid_transfer"] + t["on_credit"], t["total"])

        sr = sales_report(self.db, self.ctx, D, D)["totals"]
        self.assertEqual((sr["n"], sr["total"], sr["returns"], sr["discount"]), (t["n"], t["total"], t["returned_total"], t["discount_total"]))
        ds = self.s.day_summary(self.ctx)
        self.assertEqual((ds["n"], ds["total"], ds["cash"], ds["card"], ds["transfer"], ds["credit"], ds["returns"]),
                         (t["n"], t["total"], t["paid_cash"], t["paid_card"], t["paid_transfer"], t["on_credit"], t["returned_total"]))

        staff = owner.staff_performance(self.db, self.ctx, D, D)["rows"][0]
        self.assertEqual((staff["n"], staff["total"], staff["discount"], staff["returns"], staff["voids_n"], staff["difference"]),
                         (t["n"], t["total"], t["discount_total"], t["returned_total"], 1, 0))
        cs = owner.customer_sales(self.db, self.ctx, D, D)
        self.assertEqual(cs["totals"]["net"], t["net"])
        self.assertEqual(cs["totals"]["credit"], t["on_credit"])
        dr = owner.discounts_report(self.db, self.ctx, D, D)
        self.assertEqual(dr["totals"]["discount"], t["discount_total"])
        self.assertEqual(sum(g["discount"] for g in dr["by_who"]), t["discount_total"])
        self.assertEqual({g["key"] == "contract" for g in dr["by_who"]}, {True, False})
        rr = owner.returns_report(self.db, self.ctx, D, D)
        self.assertEqual(rr["totals"]["total"], t["returned_total"])
        sh = owner.shift_closings(self.db, self.ctx, D, D)["rows"][0]
        self.assertEqual((sh["expected_cash"], sh["counted_cash"], sh["difference"]), (self.expected_cash, self.expected_cash, 0))
        self.assertEqual(sh["sales_n"], 7)

        p = fin.profit(self.db, self.ctx, D, D)
        self.assertEqual(p["revenue"] + p["delivery_fees"], t["net"])
        self.assertEqual(p["gross_profit"], t["profit"] + t["delivery_fee"])         # same cost basis in both screens
        self.assertEqual(p["net_profit"], p["gross_profit"] - 500)

    def test_ledgers(self):
        bal = self.db.one("SELECT coalesce(sum(amount),0) b FROM customer_ledger WHERE customer_id=?", (self.cust,))["b"]
        self.assertEqual(bal, 4000 - 2000 - 1500)
        sold = 2 * 7 - 2                                                       # seven sales of 2, two units returned
        stock = self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE item_id=?", (self.pan,))["q"]
        moved = self.db.one("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE item_id=?", (self.pan,))["q"]
        self.assertEqual((stock, moved), (20 - sold, 20 - sold))
        cash = self.db.one("SELECT coalesce(sum(amount),0) a FROM cash_movements", ())["a"]
        self.assertEqual(10000 + cash, self.expected_cash)


class TestReturnNextDay(TestDay):
    def test_return_counted_by_its_own_day_in_the_returns_report(self):
        from datetime import timedelta
        y = (date.today() - timedelta(days=1)).isoformat()
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (y + "T10:00:00.000000Z", self.s1["id"]))
        inv = invoices.report(self.db, self.ctx, date_from=D, date_to=D)["totals"]
        rr = owner.returns_report(self.db, self.ctx, D, D)["totals"]
        self.assertEqual(inv["returned_total"], 2000)            # returns on today's invoices only
        self.assertEqual(rr["total"], 4000)                      # every return made today
        old = invoices.report(self.db, self.ctx, date_from=y, date_to=y)["totals"]
        self.assertEqual((old["n"], old["returned_total"]), (1, 2000))
