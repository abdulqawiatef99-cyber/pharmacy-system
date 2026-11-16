"""Deep review R7 (16/11): report rows follow each sale's own local time (daylight saving), the expiry list hides
hidden items from people without the permission, and the unsold report values stock by the sale's own rule."""
import os
import time
import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import people
from app.inventory import service as inv
from app.reports import owner
from app.reports.service import sales_report, stock_report
from app.sales.service import retail_value
from tests.helpers import DBTestCase
from tests.test_cashier_acceptance import Base

PW = "Asst-Pass-2026x"


@unittest.skipUnless(hasattr(time, "tzset"), "needs a settable time zone")
class TestSummerSaleInAWinterReport(Base):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Africa/Cairo"
        time.tzset()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_a_sale_just_after_midnight_in_summer_stays_on_its_own_day_and_hour(self):
        self.cd.open(self.ctx, 0)
        s = self.sell(units=2)
        # 00:30 local on 16 July 2026 (summer time, UTC+3); the report is read in any season
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at='2026-07-15T21:30:00.000000Z' WHERE id=?", (s["id"],))
        r = sales_report(self.db, self.ctx, "2026-07-16", "2026-07-16")
        self.assertEqual(r["totals"]["n"], 1)
        self.assertEqual([(d["day"], d["n"]) for d in r["daily"]], [("2026-07-16", 1)])
        self.assertEqual([(h["hour"], h["n"]) for h in r["by_hour"]], [(0, 1)])
        self.assertEqual(sum(d["total"] for d in r["daily"]), r["totals"]["total"])
        d = owner.discounts_report(self.db, self.ctx, "2026-07-16", "2026-07-16")
        self.assertEqual([(x["day"], x["n"]) for x in d["daily"]], [("2026-07-16", 1)])

    def test_a_winter_sale_keeps_its_day_and_hour(self):
        self.cd.open(self.ctx, 0)
        s = self.sell(units=1)
        with self.db.transaction():       # 23:45 local on 10 January 2026 (UTC+2)
            self.db.execute("UPDATE sales SET completed_at='2026-01-10T21:45:00.000000Z' WHERE id=?", (s["id"],))
        r = sales_report(self.db, self.ctx, "2026-01-10", "2026-01-10")
        self.assertEqual([(d["day"], d["n"]) for d in r["daily"]], [("2026-01-10", 1)])
        self.assertEqual([(h["hour"], h["n"]) for h in r["by_hour"]], [(23, 1)])


class TestHiddenItemsAndShelfValue(DBTestCase):
    def test_expiry_list_hides_hidden_items_without_the_permission(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "SecretHiddenDrug", "pack_size": 1, "public_price": 1000,
                                                 "hidden": True})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=20)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        people.add_user(self.db, self.ctx, username="asst1", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst1", PW)
        self.assertFalse(a.can("items.hidden"))
        self.assertEqual([r["name_en"] for r in stock_report(self.db, a)["expiring"]], [])
        self.assertEqual([r["name_en"] for r in stock_report(self.db, self.ctx)["expiring"]], ["SecretHiddenDrug"])

    def test_unsold_report_values_stock_like_a_sale(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "ThreePack", "pack_size": 3, "public_price": 1001})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=2, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("1"))
        today = date.today().isoformat()
        rows = [r for r in owner.unsold_items(self.db, self.ctx, today, today)["rows"] if r["name_en"] == "ThreePack"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_value"], retail_value(2, 1001, 3))
        self.assertEqual(rows[0]["price_value"], 668)


if __name__ == "__main__":
    unittest.main()
