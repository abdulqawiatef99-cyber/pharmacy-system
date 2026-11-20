"""Part 3 item 4: day health at shift close (idea 28) and the PC health check (idea 30). Read only."""
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.catalog import service as cat
from app.core import people
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.reports import dayhealth
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 0)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Test tab", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=10, movement_type="opening",
                              unit_cost=Decimal("5"))

    def by_key(self, checks):
        return {c["key"]: c for c in checks}


class TestDayChecks(Base):
    def test_a_clean_day_is_all_ok(self):
        got = self.by_key(dayhealth.day_checks(self.db, self.ctx, backup={"last_good_at": "2026-11-21T10:00:00"}))
        self.assertEqual({k: c["status"] for k, c in got.items()},
                         {"backup": "ok", "stock": "ok", "drafts": "ok", "transfers": "ok", "expenses": "ok",
                          "returns": "ok", "alerts": "ok"})

    def test_problems_come_first_and_say_what_to_do(self):
        s = Sales(self.db)
        sid = s.create(self.ctx)
        s.add_line(self.ctx, sid, self.item, units=1)                  # a draft left open
        done = s.create(self.ctx)
        s.add_line(self.ctx, done, self.item, units=2)
        s.complete(self.ctx, done, paid_cash=2000)
        s.void(self.ctx, done, "غلط في الصنف")
        checks = dayhealth.day_checks(self.db, self.ctx, backup={"failed": True})
        self.assertEqual([c["status"] for c in checks][:1], ["bad"])
        got = self.by_key(checks)
        self.assertEqual(got["backup"]["status"], "bad")
        self.assertEqual(got["drafts"]["status"], "warn")
        self.assertIn("1 فاتورة", got["drafts"]["text"])
        self.assertIn("1 فاتورة ملغاة", got["returns"]["text"])

    def test_money_only_for_who_sees_totals(self):
        s = Sales(self.db)
        sid = s.create(self.ctx)
        line = s.add_line(self.ctx, sid, self.item, units=1)
        s.complete(self.ctx, sid, paid_cash=1000)
        lid = self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (sid,))["id"]
        s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="العميل غير رأيه")
        self.assertIn("بـ 10.00 ج", self.by_key(dayhealth.day_checks(self.db, self.ctx))["returns"]["text"])
        people.add_user(self.db, self.ctx, username="cashier1", full_name="ك", password="Staff-Pass-2026",
                        role_code="assistant")
        cashier = self.login("cashier1", "Staff-Pass-2026")
        for c in dayhealth.day_checks(self.db, cashier):
            self.assertNotIn(" ج", c["text"], c)

    def test_needs_cash_close(self):
        uid = people.add_user(self.db, self.ctx, username="nobody1", full_name="ن", password="Staff-Pass-2026",
                              role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "cash.close", "deny")
        with self.assertRaises(Forbidden):
            dayhealth.day_checks(self.db, self.login("nobody1", "Staff-Pass-2026"))

    def test_writes_nothing(self):
        n = self.db.one("SELECT count(*) n FROM audit_log")["n"]
        dayhealth.day_checks(self.db, self.ctx, backup=None)
        dayhealth.pc_checks(self.db, self.tmp)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log")["n"], n)


class TestPCChecks(Base):
    def run_pc(self, free=50 * GB, system=("Windows", "10", "AMD64"), now=None):
        return self.by_key(dayhealth.pc_checks(self.db, self.tmp, disk=lambda p: Usage(100 * GB, 0, free),
                                               system=system, now=now))

    def test_healthy_pc(self):
        got = self.run_pc()
        self.assertEqual({k: c["status"] for k, c in got.items()},
                         {"disk": "ok", "db": "ok", "sqlite": "ok", "clock": "ok", "windows": "ok"})

    def test_low_disk(self):
        self.assertEqual(self.run_pc(free=3 * GB)["disk"]["status"], "warn")
        self.assertEqual(self.run_pc(free=GB // 2)["disk"]["status"], "bad")

    def test_old_or_32_bit_windows(self):
        self.assertEqual(self.run_pc(system=("Windows", "7", "AMD64"))["windows"]["status"], "bad")
        self.assertEqual(self.run_pc(system=("Windows", "10", "x86"))["windows"]["status"], "bad")
        self.assertIn("Windows 10 64-bit", self.run_pc(system=("Windows", "8.1", "AMD64"))["windows"]["text"])
        self.assertEqual(self.run_pc(system=("Windows", "11", "AMD64"))["windows"]["status"], "ok")

    def test_clock_behind_the_newest_record(self):
        past = datetime.now(timezone.utc) - timedelta(days=3)
        self.assertEqual(self.run_pc(now=past)["clock"]["status"], "bad")


class TestDayHealthAPI(Base):
    def test_route(self):
        from dataclasses import replace
        from starlette.testclient import TestClient
        from app import config
        from app.api.app import create_app
        from app.purchasing.service import Purchasing
        from tests.helpers import OWNER_PW
        s = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                    backup_dir=self.tmp / "backups")
        c = TestClient(create_app(self.db, s, Purchasing(self.db, s)))
        H = {"X-Requested-With": "pharm"}
        self.assertEqual(c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H).status_code, 200)
        r = c.get("/api/health/day", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({x["key"] for x in r.json()["pc"]}, {"disk", "db", "sqlite", "clock", "windows"})
        self.assertIn("backup", {x["key"] for x in r.json()["day"]})
