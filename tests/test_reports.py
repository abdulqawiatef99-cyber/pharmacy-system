"""Reports and home screen."""
from datetime import timedelta
from decimal import Decimal

from app.core import people
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.reports import service as rep
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class TestReports(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.a = self.login("asst", PW)

    def test_sales_report(self):
        self.sell(units=4)
        self.sell(ctx=self.a, units=2)
        r = rep.sales_report(self.db, self.ctx, T.isoformat(), T.isoformat())
        self.assertEqual((r["totals"]["n"], r["totals"]["total"]), (2, 12000))
        self.assertEqual(len(r["daily"]), 1)
        self.assertEqual(sorted(u["total"] for u in r["by_user"]), [4000, 8000])
        top = r["top_items"][0]
        self.assertEqual((top["qty"], top["revenue"], top["profit"]), (6, 12000, 3000))     # cost 15/unit
        with self.assertRaises(Forbidden):
            rep.sales_report(self.db, self.a, T.isoformat(), T.isoformat())

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
