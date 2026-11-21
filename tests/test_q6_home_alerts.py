"""Night queue item 6 (test gaps): the first screen's "needs attention" list (reports.service.home) and the stock
loss report's stock-in rows and unknown cost had no test. Each alert must appear exactly when its condition holds
and only for who may see it. Synthetic data only."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import card, service as cat
from app.core import people
from app.finance import expenses
from app.inventory import adjust, service as inv
from app.reports import service as reports
from app.sales.cash import CashDrawer
from app.sales.service import Sales, local_today
from tests.helpers import DBTestCase

PW = "Staff-Pass-2026"


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.today = local_today()
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 4000})
        people.add_user(self.db, self.ctx, username="asst1", full_name="A", password=PW, role_code="assistant")
        self.asst = self.login("asst1", PW)

    def stock(self, batch_no, days, qty=4, cost="15"):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, batch_no, (self.today + timedelta(days=days)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=qty, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal(cost) if cost else None)
        return b

    def texts(self, ctx=None):
        return [(a["level"], a["text"]) for a in reports.home(self.db, ctx or self.ctx)["alerts"]]


class HomeAlerts(Base):
    def test_quiet_pharmacy_only_asks_for_a_shift(self):
        self.stock("FAR", 400)
        self.assertEqual(self.texts(), [("warning", "لا يوجد شيفت مفتوح — البيع النقدي متوقف")])
        CashDrawer(self.db).open(self.ctx, 0)
        self.assertEqual(self.texts(), [])

    def test_expired_and_expiring_batches_with_stock(self):
        CashDrawer(self.db).open(self.ctx, 0)
        self.stock("OLD", -1)                 # expired yesterday, still on the shelf
        self.stock("SOON", 10)                # within 30 days
        self.stock("EDGE", 30)                # exactly 30 days: counts as soon
        self.stock("FAR", 40)                 # (not 31: a run across midnight would make it 30)
        t = self.texts()
        self.assertIn(("error", "1 تشغيلة انتهت صلاحيتها وبها رصيد — ارفعها من الرف"), t)
        self.assertIn(("warning", "2 تشغيلة تنتهي صلاحيتها خلال 30 يومًا"), t)
        self.assertEqual(len(t), 2)

    def test_an_empty_expired_batch_is_not_an_alert(self):
        CashDrawer(self.db).open(self.ctx, 0)
        b = self.stock("OLD", -1, qty=2)
        adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-2, batch_id=b, reason="expired")
        self.assertEqual(self.texts(), [])

    def test_shortages_and_open_deliveries(self):
        CashDrawer(self.db).open(self.ctx, 0)
        self.stock("FAR", 400, qty=10)
        card.set_shortage(self.db, self.ctx, self.pan, True)
        s = Sales(self.db)
        sid = s.create(self.ctx)
        s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"address": "شارع 1", "fee": 0}})
        d = s.add_line(self.ctx, sid, self.pan, units=2)
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})
        t = self.texts()
        self.assertIn(("warning", "1 صنف مسجل ناقصًا"), t)
        self.assertIn(("info", f"1 توصيلة مفتوحة — {d['total'] / 100:,.2f} ج مع مندوب التوصيل"), t)
        s.delivery_status(self.ctx, sid, "settled")
        card.set_shortage(self.db, self.ctx, self.pan, False)
        self.assertEqual(self.texts(), [])

    def test_pending_expense_only_for_who_approves(self):
        CashDrawer(self.db).open(self.ctx, 0)
        expenses.ensure_defaults(self.db)                       # done by the program at start
        cat_id = expenses.categories(self.db, self.ctx)[0]["id"]
        expenses.record(self.db, self.asst, category_id=cat_id, amount=5000, paid_from="outside", note="كهرباء")
        self.assertIn(("warning", "1 مصروف بانتظار موافقتك"), self.texts())
        self.assertNotIn(("warning", "1 مصروف بانتظار موافقتك"), self.texts(self.asst))

    def test_assistant_sees_no_totals(self):
        h = reports.home(self.db, self.asst)
        self.assertNotIn("sales", h)
        self.assertNotIn("notifications_unread", h)
        self.assertIn("sales", reports.home(self.db, self.ctx))


class LossReportRows(Base):
    def test_stock_found_is_counted_in_and_unknown_cost_is_flagged(self):
        b = self.stock("NOCOST", 400, qty=3, cost=None)        # no cost known for this item at all
        adjust.adjust(self.db, self.ctx, self.pan, qty_delta=2, batch_id=b, reason="found")
        adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-1, batch_id=b, reason="damaged")
        day = self.today.isoformat()
        rep = adjust.loss_report(self.db, self.ctx, day, day)
        g = {x["reason"]: x for x in rep["groups"]}
        self.assertEqual((g["found"]["qty_in"], g["found"]["qty_out"], g["found"]["unknown_cost"]), (2, 0, 1))
        self.assertEqual((g["damaged"]["qty_out"], g["damaged"]["unknown_cost"], g["damaged"]["value"]), (1, 1, 0))
        self.assertEqual(rep["total"], 0)                       # nothing invented for an unknown cost
