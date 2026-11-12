"""Reports and home screen."""
from datetime import timedelta
from decimal import Decimal

from app.core import people
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.reports import service as rep
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T
from tests.helpers import today

PW = "Staff-Pass-2026"


class TestStockValueExact(Base):
    def test_values_are_whole_piasters_half_up_from_the_decimal_cost(self):
        from app.catalog import service as cat
        it = cat.create_item(self.db, self.ctx, {"name_en": "Exact Cost", "pack_size": 2, "public_price": 1003})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "EX", (T + timedelta(days=20)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=3, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("0.145"))
        s = rep.stock_report(self.db, self.ctx)
        e = next(x for x in s["expiring"] if x["batch_no"] == "EX")
        self.assertEqual(e["value"], 1505)                 # one pack 10.03 + one unit at 5.02 (5.015 half up), as a sale charges
        # cost 20 x 15 + 3 x 0.145 = 300.435 pounds -> 30044, now summed in Decimal rather than SQLite REAL
        self.assertEqual(s["value_cost"], 30044)
        self.assertEqual(s["value_retail"], 41505)             # 40000 + 1505 in one category (round() half even gave 41504)
        self.assertEqual([r["items"] for r in s["value_by_category"]], [2])
        self.assertIsInstance(s["value_cost"], int)


    def test_retail_value_is_what_a_sale_of_those_units_charges(self):
        from app.catalog import service as cat
        from app.sales.service import alloc_gross, unit_price
        it = cat.create_item(self.db, self.ctx, {"name_en": "Shelf Rule", "pack_size": 3, "public_price": 1001})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "SR", (T + timedelta(days=10)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=2, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("1"))
        e = next(x for x in rep.stock_report(self.db, self.ctx)["expiring"] if x["batch_no"] == "SR")
        # two loose units sell at 3.34 each (10.01 / 3 = 3.3367 half up): 6.68, not 2 x 10.01 / 3 = 6.67
        self.assertEqual(e["value"], 668)
        self.assertEqual(e["value"], alloc_gross(2, {"unit_price": unit_price(1001, 3), "pack_price": 1001, "pack_size": 3}))


    def test_one_item_over_two_batches_is_priced_as_one_quantity(self):
        from app.catalog import service as cat
        it = cat.create_item(self.db, self.ctx, {"name_en": "Two Batches", "pack_size": 3, "public_price": 1001})
        before = rep.stock_report(self.db, self.ctx)["value_retail"]
        with self.db.transaction():
            for no, q in (("TB1", 1), ("TB2", 2)):
                b = inv.get_or_create_batch(self.db, self.ctx, it, no, (T + timedelta(days=200)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=q, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("1"))
        # 1 + 2 units of a pack of 3 are one whole pack (10.01), not 3 loose units at 3.34 (10.02)
        self.assertEqual(rep.stock_report(self.db, self.ctx)["value_retail"] - before, 1001)


class TestReports(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.a = self.login("asst", PW)

    def test_sales_report(self):
        self.sell(units=4)
        self.sell(ctx=self.a, units=2)
        r = rep.sales_report(self.db, self.ctx, today(), today())
        self.assertEqual((r["totals"]["n"], r["totals"]["total"]), (2, 12000))
        self.assertEqual(len(r["daily"]), 1)
        self.assertEqual(sorted(u["total"] for u in r["by_user"]), [4000, 8000])
        top = r["top_items"][0]
        self.assertEqual((top["qty"], top["revenue"], top["profit"]), (6, 12000, 3000))     # cost 15/unit
        with self.assertRaises(Forbidden):
            rep.sales_report(self.db, self.a, today(), today())

    def test_stock_and_home(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "OLD", (T + timedelta(days=10)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        s = rep.stock_report(self.db, self.ctx)
        self.assertEqual([e["batch_no"] for e in s["expiring"]], ["OLD"])
        self.assertEqual(s["value_cost"], 24 * 1500)
        self.assertNotIn("value_cost", rep.stock_report(self.db, self.a))
        h = rep.home(self.db, self.ctx)
        self.assertTrue(any("30 يوم" in a["text"] for a in h["alerts"]))
        self.assertIn("sales", h)
        self.assertNotIn("sales", rep.home(self.db, self.a))


class TestHomeAPI(APICase):
    def test_home(self):
        self.http_login()
        r = self.c.get("/api/home").json()
        self.assertFalse(r["shift_open"])
        self.assertIn("backup", r)


class TestFeedback(Base):
    def test_feedback(self):
        from app.core import feedback as fb
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        fb.add(self.db, a, "suggestion", "عايز زرار يطبع الفاتورة مرتين", "#/pos")
        with self.assertRaises(Exception):
            fb.add(self.db, a, "x", "نص")
        self.assertEqual(len(fb.listing(self.db, self.ctx)), 1)
        self.assertEqual(len(fb.listing(self.db, a)), 1)
        with self.assertRaises(Forbidden):
            fb.set_status(self.db, a, fb.listing(self.db, a)[0]["id"], "done")
        fb.set_status(self.db, self.ctx, fb.listing(self.db, a)[0]["id"], "done", "اتعمل")
        self.assertEqual(fb.listing(self.db, a)[0]["reply"], "اتعمل")


class TestNetworkAPI(APICase):
    def test_lan_toggle_and_launcher_reads_it(self):
        self.http_login()
        self.assertFalse(self.c.get("/api/system/network").json()["lan"])
        r = self.c.put("/api/system/network", json={"lan": True}, headers=H).json()
        self.assertTrue(r["lan"])
        self.assertNotIn("restarting", r)                  # not under the launcher in tests
        import launcher
        home = self.tmp / "home"
        (home / "data").mkdir(parents=True)
        (home / "data" / "network.json").write_text((self.s.data_dir / "network.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertTrue(launcher.lan_enabled(home))
        self.assertFalse(launcher.lan_enabled(self.tmp / "nowhere"))
