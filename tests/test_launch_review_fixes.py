"""Final pre-launch review (18/11): P1-1..P1-4, P2-5, P2-7. Each test failed on `develop` @ 5d00d45."""
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.catalog import service as cat
from app.core.errors import Conflict
from app.dataimport.service import DataImport, _whole_units
from tests.helpers import OWNER_PW, DBTestCase
from tests.test_cashier_acceptance import Base
from tests.test_estock import xlsx

ROOT = Path(__file__).resolve().parents[1]


def _complete(t, sid, total, **kw):
    f = t.s.safety_check(t.ctx, sid)
    return t.s.complete(t.ctx, sid, payments=[{"method": "cash", "amount": total}],
                        safety_decisions={i: "ok" for i in range(len(f))}, **kw)


class TestVoidStoreSaleAfterDeliveryToggle(Base):
    """P1-1: a sale switched to delivery and back to store kept its delivery draft row; void then took the
    courier branch and left the sale's cash in the drawer (100 -> 140 -> still 140 after the void)."""

    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 10000)

    def drawer(self):
        return self.cd.summary(self.ctx)["expected_cash"]

    def test_probe_drawer_back_to_the_opening_cash(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2)                                  # 40.00
        self.s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"phone": "0100", "fee": 0}})
        d = self.s.set_details(self.ctx, sid, {"kind": "store"})
        self.assertIsNone(d["delivery"])                                                    # draft row gone
        _complete(self, sid, d["total"])
        self.assertEqual(self.drawer(), 14000)
        self.s.void(self.ctx, sid, "خطأ في الفاتورة")
        self.assertEqual(self.drawer(), 10000)

    def test_store_sale_with_a_leftover_delivery_row_still_takes_its_cash_out(self):
        """A row left by an older program version (before the kind switch removed it): void follows the
        sale's kind, not the presence of the row."""
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        _complete(self, sid, d["total"])
        with self.db.transaction():
            self.db.execute("INSERT INTO sale_deliveries (sale_id, org_id) VALUES (?,?)", (sid, self.ctx.org_id))
        self.s.void(self.ctx, sid, "خطأ في الفاتورة")
        self.assertEqual(self.drawer(), 10000)

    def test_delivery_sale_void_before_settlement_still_moves_no_cash(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        d = self.s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"phone": "0100"}})
        _complete(self, sid, d["total"])
        self.assertEqual(self.drawer(), 10000)                                              # with the courier
        self.s.void(self.ctx, sid, "العميل لغى الطلب")
        self.assertEqual(self.drawer(), 10000)
        st = self.db.one("SELECT status FROM sale_deliveries WHERE sale_id=?", (sid,))["status"]
        self.assertEqual(st, "cancelled")


class TestReturnWaitsForDeliverySettlement(Base):
    """P1-2: a return on a delivery sale the courier has not settled is refused, whatever the refund
    method (stock, debt and money stay as they are), with a clear message; after settlement it works."""

    def test_refused_before_settlement_then_allowed(self):
        self.cd.open(self.ctx, 10000)
        cust = self.s.create_customer(self.ctx, name="عميل تجربة", credit_limit=100000)
        sid = self.s.create(self.ctx, cust)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        d = self.s.set_details(self.ctx, sid, {"kind": "delivery", "delivery": {"phone": "0100"}})
        r = _complete(self, sid, d["total"])
        line = r["lines"][0]["id"]
        for method in ("cash", "card", "credit"):
            with self.subTest(method=method), self.assertRaises(Conflict) as e:
                self.s.return_items(self.ctx, sid, {line: 1}, refund_method=method, reason="مرتجع تجربة")
            self.assertEqual(e.exception.code, "delivery_not_settled")
            self.assertIn("سجّل تسليم مبلغ التوصيل أولًا", str(e.exception))
        self.assertEqual(self.db.one("SELECT count(*) FROM sale_returns")[0], 0)
        self.assertEqual(self.db.one("SELECT sum(returned_qty) FROM sale_allocations WHERE sale_line_id=?",
                                     (line,))[0], 0)
        self.s.delivery_status(self.ctx, sid, "delivered")
        with self.assertRaises(Conflict):                         # delivered is not settled: cash not in yet
            self.s.return_items(self.ctx, sid, {line: 1}, refund_method="credit", reason="مرتجع تجربة")
        self.s.delivery_status(self.ctx, sid, "settled")
        out = self.s.return_items(self.ctx, sid, {line: 1}, refund_method="cash", reason="مرتجع تجربة")
        self.assertEqual(out["refund"], 2000)
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 10000 + 4000 - 2000)


class TestUnknownPackIsNotRounded(DBTestCase):
    """P1-3: with pack 1 (pack size not known) the 0.15 E-Stock drift tolerance turned 2.1 packs into 2
    units. The tolerance is for split items only."""

    def test_whole_units(self):
        mue = Decimal("0.15")
        for q in ("2.1", "2.125", "2.9"):
            self.assertIsNone(_whole_units(Decimal(q), 1, mue), q)
        self.assertEqual(_whole_units(Decimal("2"), 1, mue), 2)
        self.assertEqual(_whole_units(Decimal("2.0004"), 1, mue), 2)        # the export's 3-decimal noise
        self.assertEqual(_whole_units(Decimal("0.623"), 24, mue), 15)        # split-item drift still accepted
        self.assertIsNone(_whole_units(Decimal("0.623"), 24))                 # ... only when asked for

    def test_import_lists_them_as_unknown_pack(self):
        di = DataImport(self.db)
        run = di.analyze(self.ctx, xlsx([["الكود", "الاسم الانجليزي"], [1, "Item A"], [2, "Item B"],
                                         [3, "Item C"]]), "i.xlsx", kind="items")
        di.apply(self.ctx, run)
        stock = xlsx([["الكود", "الكمية", "التكلفة"], [1, 2.1, 30], [2, 2.125, 30], [3, 2.9, 30]])
        run = di.analyze(self.ctx, stock, "s.xlsx", kind="opening_stock", options={"max_unit_error": "0.15"})
        rows = di.get(self.ctx, run)["rows"]
        self.assertEqual([r["action"] for r in rows], ["error"] * 3)
        for r in rows:
            self.assertEqual([i["code"] for i in r["issues"]], ["fractional_qty_needs_pack_size"])


class TestFreshStartKeepsBackupSettings(DBTestCase):
    """P1-4: the backup folder and schedule chosen by the owner (data\\backup_settings.json) survive the
    launch-night fresh start."""

    def test_settings_files_copied(self):
        text = (ROOT / "scripts" / "windows" / "launch-fresh-start.bat").read_text(encoding="ascii")
        line = next(l for l in text.splitlines() if l.startswith("for %%n in ("))
        names = line.split("(", 1)[1].split(")", 1)[0].split()
        self.assertEqual(names, ["installed_version.json", "network.json", "backup_settings.json"])
        from app.core.autobackup import AutoBackup
        self.assertEqual(AutoBackup(self.db, self.tmp, self.tmp, self.tmp).cfg_path, self.tmp / "backup_settings.json")


class TestRunbookReSetupChecklist(DBTestCase):
    """P1-5: RELEASE_RUNBOOK §10 lists what the owner sets again on the new database, after step B."""

    def test_checklist_after_step_b(self):
        text = (ROOT / "docs" / "RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
        sec = text.split("## 10.", 1)[1]
        b, c = sec.index("### ب."), sec.index("### ج.")
        part = sec[b:c]
        for word in ("طابعة الإيصال", "الدرج", "طابعة الملصقات", "ملصق تجربة", "اللوجو", "QR",
                     "حسابات الموظفين", "الصلاحيات", "فولدر النسخ الاحتياطي"):
            self.assertIn(word, part, word)
        self.assertIn("آخر نسخة خارج الجهاز", sec[c:])


class TestImportOnlyIntoAnEmptyDatabase(DBTestCase):
    """P2-5: `manage.py import-estock` (estock-import.bat) refuses a database that already has items or
    stock movements; the documented re-run after a failed stock step is still allowed."""

    def setUp(self):
        super().setUp()
        import manage
        self.manage = manage
        self.cat = self.tmp / "catalog.xlsx"
        self.cat.write_bytes(xlsx([["الكود", "الاسم الانجليزي"], [1, "Item A"]]))
        (self.tmp / "stock.xlsx").write_bytes(b"")

    def run_cmd(self, apply):
        a = SimpleNamespace(username="owner", password=OWNER_PW, catalog=str(self.cat),
                            stock=str(self.tmp / "stock.xlsx"), packs=None, apply=apply, backup_dir=None,
                            store=None, accept_zero_cost=True, max_unit_error="0.15")
        from app.core import backup
        from app.dataimport import estock_job
        with mock.patch.object(self.manage, "_db", return_value=self.db), \
                mock.patch.object(estock_job, "run", side_effect=AssertionError("import ran")) as job, \
                mock.patch.object(backup, "create", side_effect=AssertionError("backup ran")):
            with self.assertRaises(SystemExit) as e:
                self.manage.cmd_import_estock(a, SimpleNamespace())
        self.assertFalse(job.called)
        return str(e.exception.code)

    def test_items_already_there(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Old item", "pack_size": 1, "public_price": 100})
        for apply in (False, True):
            self.assertIn("فيها أصناف بالفعل", self.run_cmd(apply))

    def test_stock_movements_already_there(self):
        from app.inventory import service as inv
        item = cat.create_item(self.db, self.ctx, {"name_en": "Old item", "pack_size": 1, "public_price": 100})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=3, movement_type="opening",
                              unit_cost=Decimal("1"))
        self.assertIn("حركات مخزون", self.run_cmd(True))

    def test_empty_database_and_rerun_after_the_catalog_step_pass(self):
        ctx = self.ctx
        self.assertIsNone(self.manage._estock_import_refused(self.db, ctx, self.cat))
        di = DataImport(self.db)
        di.apply(ctx, di.analyze(ctx, self.cat.read_bytes(), "catalog.xlsx", kind="items"))
        self.assertIsNone(self.manage._estock_import_refused(self.db, ctx, self.cat))       # same file re-run
        other = self.tmp / "other.xlsx"
        other.write_bytes(xlsx([["الكود", "الاسم الانجليزي"], [2, "Item B"]]))
        self.assertIn("فيها أصناف بالفعل", self.manage._estock_import_refused(self.db, ctx, other))

    def test_bat_still_calls_the_guarded_command(self):
        text = (ROOT / "scripts" / "windows" / "estock-import.bat").read_text(encoding="ascii")
        self.assertEqual(text.count('"%MANAGE%" import-estock'), 2)          # dry run + apply, both guarded
        src = (ROOT / "manage.py").read_text(encoding="utf-8")
        body = src.split("def cmd_import_estock", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(body.index("_estock_import_refused("), body.index("backup.create("))
        self.assertLess(body.index("_estock_import_refused("), body.index("db.backup_to("))


class TestPerfScriptsLeaveTheTimeZoneAlone(DBTestCase):
    """P2-7: importing the perf scripts (the test suite does) set TZ=Africa/Cairo for the whole process, so
    the local-day tests that ran after them failed near midnight. Only main() sets it now."""

    def test_import_does_not_set_tz(self):
        env = {k: v for k, v in os.environ.items() if k != "TZ"}
        code = ("import os, sys; sys.path.insert(0, sys.argv[1]);"
                " import bench, loadgen, real_years, serve_at, scale; print(repr(os.environ.get('TZ')))")
        r = subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts" / "perf")], cwd=tempfile.gettempdir(),
                           env={**env, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "None")

    def test_main_still_runs_on_cairo_time(self):
        for n in ("bench", "loadgen", "real_years", "serve_at"):
            src = (ROOT / "scripts" / "perf" / f"{n}.py").read_text(encoding="utf-8")
            main = src.split("\ndef main(", 1)[1]
            self.assertTrue(main.split("\n", 2)[1].strip() == "use_cairo_time()", n)
            self.assertNotIn('\nos.environ.setdefault("TZ"', src)
            self.assertNotIn("\nif hasattr(time, \"tzset\")", src)
        scale = (ROOT / "scripts" / "perf" / "scale.py").read_text(encoding="utf-8")
        self.assertIn("def main(argv=None):\n    ry.use_cairo_time()", scale)
