"""Sales report, best-selling items: the profit used SQLite REAL sums of the decimal unit cost and
round() (half to even). One unit costing 0.125 sold for 1.00 showed 0.88 profit; the finance profit
report says 0.87. Now exact Decimal, half up, the same number. Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.finance import expenses
from app.inventory import service as inv
from app.reports import service as rep
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase


class TopItemsProfit(DBTestCase):
    def sell(self, item, units):
        s = Sales(self.db)
        sid = s.create(self.ctx, None)
        d = s.add_line(self.ctx, sid, item, units=units)
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=d["total"], paid_card=0, on_credit=0,
                   safety_decisions={i: "ok" for i in range(len(f))})

    def buy(self, item, batch, cost):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, item, batch, (date.today() + timedelta(days=300)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=1, batch_id=b, movement_type="purchase",
                              unit_cost=cost)

    def test_profit_is_exact_half_up_and_matches_the_finance_report(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Eighth", "pack_size": 1, "public_price": 100})
        other = cat.create_item(self.db, self.ctx, {"name_en": "No Cost", "pack_size": 1, "public_price": 300})
        self.buy(it, "T0", Decimal("0.125"))
        self.buy(other, "T2", None)
        CashDrawer(self.db).open(self.ctx, 0)
        self.sell(it, 1)                                    # cost 0.125 -> 12.5 piasters
        today = date.today().isoformat()
        top = {t["item_id"]: t for t in rep.sales_report(self.db, self.ctx, today, today)["top_items"]}
        self.assertEqual((top[it]["revenue"], top[it]["profit"]), (100, 87))
        self.assertEqual(expenses.profit(self.db, self.ctx, today, today)["gross_profit"], 87)
        self.buy(it, "T1", Decimal("0.145"))                # a second unit: the two exact costs are added, then rounded once
        self.sell(it, 1)
        top = {t["item_id"]: t for t in rep.sales_report(self.db, self.ctx, today, today)["top_items"]}
        cost2 = self.db.one("SELECT unit_cost FROM sale_allocations a JOIN sale_lines l ON l.id=a.sale_line_id"
                            " WHERE l.item_id=? ORDER BY a.rowid DESC LIMIT 1", (it,))["unit_cost"]
        self.assertEqual(top[it]["profit"], 200 - int((Decimal("12.5") + Decimal(cost2) * 100 + Decimal("0.5")) // 1))
        self.sell(other, 1)                                 # unknown cost: no profit shown
        top = {t["item_id"]: t for t in rep.sales_report(self.db, self.ctx, today, today)["top_items"]}
        self.assertNotIn("profit", top[other])
