"""Codex review #324 parts 2-4, group A (launch night): one test per finding. Synthetic data only.

Every test here failed on develop @ 1de18b8 (verified) and passes after the fix."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.dataimport import estock, estock_job
from app.dataimport.service import DataImport
from app.recalls.service import Recalls
from tests.helpers import DBTestCase
from tests.test_dataimport import ITEMS, sheet
from tests.test_estock import CATALOG, stock_rows, xlsx

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / "scripts" / "windows"


def stock_file(stores: dict[str, list[tuple]]) -> list[list]:
    """E-Stock stock-by-expiry layout with matching totals. Rows: (code, name, qty, cost, sale, expiry)."""
    out = [["صــيــدلــيــة"] + [None] * 14]
    gs = gc = 0
    for i, (store, rows) in enumerate(stores.items(), start=1):
        out.append([store, "اسم المخزن", i, "كود المخزن"] + stock_rows()[1][4:])
        ss = sc = 0
        for code, name, qty, cost, sale, exp in rows:
            out.append([10, round(qty * sale, 4), round(qty * cost, 4), sale, cost, qty, exp, "علبة",
                        "غير معرفة", name, code, None, None, None, None])
            ss += qty * sale
            sc += qty * cost
        out.append([round(ss, 4), "إجمالى البيع", round(sc, 4), "إجمالى التكلفة", len({r[0] for r in rows}),
                    "عدد الاصناف", None, None, None, None, None, None, None, None, None])
        gs += ss
        gc += sc
    out.append(["إجمالى جميع المخازن", round(gs, 4), "إجمالى البيع", round(gc, 4), "إجمالى التكلفة", 0,
                "عدد الاصناف", None, None, None, None, None, None, None, None])
    return out


class _Job(DBTestCase):
    def run_job(self, stock, packs=None, stores=("الصيدلية",)):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "c.xlsx").write_bytes(xlsx(CATALOG))
        (d / "s.xlsx").write_bytes(xlsx(stock))
        (d / "p.json").write_text(json.dumps({"packs": packs or {"30906": 3}}), encoding="utf-8")
        return estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                              packs_file=d / "p.json", stores=list(stores))

    def units(self, code):
        return self.db.one("SELECT coalesce(sum(m.qty_delta),0) q FROM stock_movements m JOIN items i"
                           " ON i.id=m.item_id WHERE i.org_id=? AND i.code=?", (self.ctx.org_id, code))["q"]


class TestS10R1StoreSpelling(_Job):
    """S10 / R1: the selected store is «الصيدلية», the file spells it «الصيدليه» (ha for ta marbuta)."""

    def test_store_spelled_with_ha_still_imports_its_stock(self):
        rows = stock_rows()
        rows[1][0] = "الصيدليه"
        rep = self.run_job(rows)
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2)
        self.assertEqual(self.units("30906"), 47)

    def test_spaces_tatweel_and_alef_forms_match_too(self):
        rows = stock_rows()
        rows[1][0] = " الصــيدلية "
        rep = self.run_job(rows, stores=("الصيدليّة",))
        self.assertEqual(rep["stock"]["applied"]["stock_rows"], 2)

    def test_zero_selected_rows_from_a_file_with_stock_fails_loudly_and_applies_nothing(self):
        rows = stock_rows()
        rows[1][0] = "المخزن الرئيسي"
        with self.assertRaises(ValidationFailed) as e:
            self.run_job(rows, stores=("الصيدلية", "مستشفيات2"))
        self.assertEqual(e.exception.code, "estock_no_rows_for_store")
        self.assertIn("المخزن الرئيسي", str(e.exception))
        self.assertEqual(self.db.one("SELECT count(*) n FROM import_runs WHERE kind='opening_stock'"
                                     " AND status='applied'")["n"], 0)
        # the corrected store name then imports the same file (no "already applied")
        rep = self.run_job(rows, stores=("المخزن الرئيسي",))
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2)


class TestS11TwoStoresSameBatch(_Job):
    def test_same_code_expiry_and_cost_in_two_selected_stores_posts_both(self):
        exp = datetime(2028, 3, 1)
        rows = stock_file({"الصيدلية": [(1, "ONE TWO THREE 20TAB", 2, 20, 40, exp)],
                           "مستشفيات": [(1, "ONE TWO THREE 20TAB", 1, 20, 40, exp)]})
        rep = self.run_job(rows, stores=("الصيدلية", "مستشفيات"))
        self.assertTrue(rep["stock"]["reconciliation"]["ok"])
        self.assertFalse(rep["stock"].get("skipped_review"))
        self.assertEqual(self.units("1"), 3)


class TestS12FractionalResidue(_Job):
    def test_a_residue_that_rounds_to_zero_units_is_skipped_not_fatal(self):
        exp = datetime(2028, 3, 1)
        rows = stock_file({"الصيدلية": [(30906, "LITHIOMOD 400MG 30TAB", 0.001, 100, 147, exp),
                                         (1, "ONE TWO THREE 20TAB", 2, 20, 40, exp)]})
        rep = self.run_job(rows)
        self.assertTrue(rep["stock"]["reconciliation"]["ok"])
        self.assertEqual(self.units("1"), 2)                       # the valid row is in
        self.assertEqual(self.units("30906"), 0)
        self.assertEqual([x["code"] for x in rep["stock"]["rounded_to_zero"]], ["30906"])


class TestS2OpeningStockMeetsRecalls(DBTestCase):
    def test_opening_stock_of_a_recalled_lot_raises_a_recall_alert(self):
        di = DataImport(self.db)
        di.apply(self.ctx, di.analyze(self.ctx, sheet(ITEMS[:4]), "items.xlsx"))
        Recalls(self.db).add_notice(self.ctx, title="منشور سحب",
                                    items=[{"product_name": "Panadol 500mg", "batch_numbers": ["AB01"]}])
        data = sheet([["كود", "الصنف", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"],
                      [101, "Panadol", 1.5, 32, "AB01", "05/2028"]])
        di.apply(self.ctx, di.analyze(self.ctx, data, "stock.xlsx", kind="opening_stock"))
        alerts = Recalls(self.db).alerts(self.ctx)
        self.assertEqual([(a["batch_no"], a["qty_in_stock"]) for a in alerts], [("AB01", 3)])


class TestS33FractionalBarcode(DBTestCase):
    def test_a_fractional_barcode_is_invalid_and_never_matches_another_item(self):
        di = DataImport(self.db)
        old = cat.create_item(self.db, self.ctx, {"name_en": "Old Product", "pack_size": 1})
        cat.add_barcode(self.db, self.ctx, old, "123456")
        run = di.analyze(self.ctx, sheet([["اسم الصنف", "باركود", "سعر الجمهور"],
                                           ["New Product", 123456.7, 10]]), "items.xlsx")
        row = di.get(self.ctx, run)["rows"][0]
        self.assertIn("invalid_barcode", [i["code"] for i in row["issues"]])
        self.assertIsNone(row["target_id"])
        for r in self.db.all("SELECT id FROM import_rows WHERE run_id=? AND action='review'", (run,)):
            di.decide(self.ctx, run, r["id"], "create")
        di.apply(self.ctx, run)
        self.assertEqual(cat.get_item(self.db, self.ctx, old)["name_en"], "Old Product")


class TestR2FreshStartAfterLaunch(DBTestCase):
    """R2: launch-fresh-start.bat must refuse once the imported database has a sale, and needs the
    pharmacy's name typed (not just YES). The check lives in `manage.py fresh-start-check`."""

    def check(self, answer):
        from manage import fresh_start_refusal
        return fresh_start_refusal(self.db, answer)

    def test_trial_database_with_sales_but_no_import_is_allowed_with_the_name(self):
        self.assertIsNone(self.check("صيدلية الاختبار"))
        self.assertIsNone(self.check("  صيدليه الاختبار "))      # spelling forms do not matter

    def test_wrong_name_or_yes_is_refused(self):
        self.assertIn("اسم الصيدلية", self.check("YES"))
        self.assertIn("اسم الصيدلية", self.check(""))

    def test_a_sale_after_the_opening_stock_import_refuses(self):
        from tests.test_review_324_money import _sell, _stock
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        di = DataImport(self.db)
        di.apply(self.ctx, di.analyze(self.ctx, sheet(ITEMS[:4]), "items.xlsx"))
        data = sheet([["كود", "الصنف", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"],
                      [101, "Panadol", 5, 32, "P1", "05/2028"]])
        di.apply(self.ctx, di.analyze(self.ctx, data, "stock.xlsx", kind="opening_stock"))
        self.assertIsNone(self.check("صيدلية الاختبار"))          # imported, nothing sold yet: allowed
        CashDrawer(self.db).open(self.ctx, 0)
        item = self.db.one("SELECT id FROM items WHERE code='101'")["id"]
        _sell(Sales(self.db), self.ctx, item, 1)
        why = self.check("صيدلية الاختبار")
        self.assertIsNotNone(why)
        self.assertIn("بيع", why)

    def test_the_bat_runs_the_check_before_anything_is_changed(self):
        t = (WIN / "launch-fresh-start.bat").read_text(encoding="utf-8")
        self.assertTrue("fresh-start-check" in t)
        self.assertLess(t.index("fresh-start-check"), t.index("call :stopprog"))
        self.assertNotIn('"%ANS%"=="YES"', t)                     # the YES prompt is gone
        rb = (ROOT / "docs" / "RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
        self.assertNotIn("ساعتها ارجع للخطوة ب", rb)


class TestR2FreshStartCheckCommand(unittest.TestCase):
    def test_the_command_exits_1_on_a_wrong_name_and_0_on_the_right_one(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_")}
            env.update(PHARM_DATA_DIR=d, PYTHONUTF8="1")
            run = lambda *a, **e: subprocess.run([sys.executable, str(ROOT / "manage.py"), *a], cwd=ROOT,
                                                 env={**env, **e}, capture_output=True, text=True,
                                                 encoding="utf-8", stdin=subprocess.DEVNULL)
            r = run("init", "--pharmacy", "صيدلية النور", "--username", "owner", "--password", "Owner-Pass-2026")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(run("fresh-start-check", PHARM_E2E_PHARMACY="YES").returncode, 1)
            self.assertEqual(run("fresh-start-check").returncode, 1)            # nothing typed
            r = run("fresh-start-check", PHARM_E2E_PHARMACY="صيدليه النور")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestS41S42R14Scripts(unittest.TestCase):
    """S41: fresh start probes a hard-coded 8000; S42 / R14: estock-import.bat says PASS - ALL DONE without
    checking the program answers, and its stop check passes when a foreign server answers 404."""

    def bat(self, name):
        return (WIN / name).read_text(encoding="utf-8")

    def test_no_script_hard_codes_port_8000(self):
        for name in ("launch-fresh-start.bat", "estock-import.bat"):
            t = self.bat(name)
            self.assertFalse("127.0.0.1:8000" in t, name)
            self.assertTrue('if not defined PHARM_PORT set "PORT=8000"' in t, name)

    def test_import_checks_health_before_all_done(self):
        t = self.bat("estock-import.bat")
        start = t.index('start "" /d "%INST%\\program" "%PYW%"')
        health = t.index("/api/health", start)
        self.assertLess(health, t.index("ALL DONE"))
        self.assertIn("program did not answer", t[start:t.index("ALL DONE")])

    def test_r13_a_settings_copy_that_fails_is_a_fail(self):
        line = next(l for l in self.bat("launch-fresh-start.bat").splitlines() if "backup_settings.json" in l
                    and l.startswith("for "))
        self.assertTrue(line.endswith('|| (set "MSG=could not copy the settings file %%n" & goto fail)'), line)

    def test_stop_check_counts_any_listener_not_only_a_200(self):
        for name in ("launch-fresh-start.bat", "estock-import.bat"):
            stop = self.bat(name).split("\n:stopprog\n", 1)[1]
            self.assertTrue("TcpClient" in stop, name)
            self.assertFalse("Invoke-WebRequest" in stop, name)


if __name__ == "__main__":
    unittest.main()
