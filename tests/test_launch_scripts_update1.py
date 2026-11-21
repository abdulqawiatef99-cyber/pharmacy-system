"""Update 1 (launch day 21/11): the launch scripts.
- an old cmd window shows Arabic printed by Python as ???: prompts are English, Arabic goes to Notepad;
- estock-db-import.bat asks the password ONCE, visibly (Python's hidden prompt writes to the window itself), and
  hands it to the dry run and the move through PHARM_OWNER_PASSWORD, never the command line;
- a stray Enter is not "no": the user name and YES are asked again;
- launch-fresh-start.bat lists the sales made after an import (dates, totals) and goes on only on YES."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.dataimport.service import DataImport
from tests.helpers import DBTestCase
from tests.test_dataimport import ITEMS, sheet

ROOT = Path(__file__).resolve().parent.parent
BAT = (ROOT / "scripts" / "windows" / "estock-db-import.bat").read_text(encoding="ascii")
MANAGE = (ROOT / "manage.py").read_text(encoding="utf-8")


def code(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().upper().startswith("REM")]


class TestImportBat(unittest.TestCase):
    def test_password_asked_once_through_the_environment(self):
        L = code(BAT)
        asks = [l for l in L if "ask-password" in l]
        self.assertEqual(len(asks), 1)
        self.assertIn('set "PHARM_OWNER_PASSWORD=%%p"', asks[0])
        stop = L.index("call :stopprog")
        self.assertLess(L.index(asks[0]), stop)                         # asked before anything is stopped
        runs = [l for l in L if '"%MANAGE%" import-estock-db' in l and "--status" not in l and "--rehearsal" not in l]
        self.assertEqual(len(runs), 2)
        for l in runs:
            self.assertNotIn("PHARM_OWNER_PASSWORD", l)                # never on the command line
        # cleared on every way out
        for label in (":fail", ":rehearsal"):
            block = L[L.index(label):]
            self.assertIn('set "PHARM_OWNER_PASSWORD="', block[:12])
        done = L.index('call :log PASS "ALL DONE - program started again"')
        self.assertEqual(L[done - 1], 'set "PHARM_OWNER_PASSWORD="')
        self.assertNotIn("password twice", BAT)

    def test_a_stray_enter_asks_again(self):
        L = code(BAT)
        i = L.index(":askyes")
        self.assertEqual(L[i + 1:i + 4], ["if defined ANS goto haveyes",
                                          'set /p "ANS=Type YES to apply the move to the real database (NO to stop): "',
                                          "goto askyes"])
        j = L.index(":askowner")
        self.assertIn("if defined OWNER goto haveowner", L[j + 1])
        self.assertIn("goto askowner", L[j + 2:j + 6])

    def test_no_arabic_output_typed_into_the_window(self):
        self.assertNotIn('type "%DRY%" &', BAT)
        self.assertNotIn('type "%APPLIED%"\r\n', BAT)
        self.assertIn('start "" notepad "%SHOW%"', BAT)


class TestManagePrompts(unittest.TestCase):
    def test_english_prompts_and_the_environment(self):
        self.assertIn('getpass.getpass(f"Password for {username} (typing is hidden): ")', MANAGE)
        self.assertNotIn('getpass.getpass(f"كلمة مرور {a.username}: ")\n    tok = accounts.login(db, a.username, pw)["token"]\n'
                         '    for f in [a.dir', MANAGE)
        import manage
        os.environ["PHARM_OWNER_PASSWORD"] = "x-secret"
        try:
            self.assertEqual(manage._owner_password("owner"), "x-secret")
        finally:
            del os.environ["PHARM_OWNER_PASSWORD"]


class TestFreshStartSales(DBTestCase):
    def sold(self):
        from tests.test_review_324_money import _sell
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        di = DataImport(self.db)
        di.apply(self.ctx, di.analyze(self.ctx, sheet(ITEMS[:4]), "items.xlsx"))
        data = sheet([["كود", "الصنف", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"],
                      [101, "Panadol", 5, 32, "P1", "05/2028"]])
        di.apply(self.ctx, di.analyze(self.ctx, data, "stock.xlsx", kind="opening_stock"))
        CashDrawer(self.db).open(self.ctx, 0)
        _sell(Sales(self.db), self.ctx, self.db.one("SELECT id FROM items WHERE code='101'")["id"], 1)

    def test_listed_and_allowed_only_after_yes(self):
        from manage import fresh_start_refusal, sales_after_import
        self.sold()
        rows = sales_after_import(self.db)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["at"] and rows[0]["total"] > 0)
        self.assertIn("بيع", fresh_start_refusal(self.db, "صيدلية الاختبار"))
        self.assertIsNone(fresh_start_refusal(self.db, "صيدلية الاختبار", sales_confirmed=True))
        self.assertIn("اسم الصيدلية", fresh_start_refusal(self.db, "YES", sales_confirmed=True))


class TestFreshStartCommand(unittest.TestCase):
    def test_yes_after_the_list_goes_on_anything_else_stops(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_")}
            env.update(PHARM_DATA_DIR=str(Path(d) / "data"), PYTHONUTF8="1")
            run = lambda *a, **e: subprocess.run([sys.executable, str(ROOT / "manage.py"), *a], cwd=ROOT,  # noqa: E731
                                                 env={**env, **e}, capture_output=True, text=True,
                                                 encoding="utf-8", stdin=subprocess.DEVNULL)
            self.assertEqual(run("init", "--pharmacy", "صيدلية النور", "--username", "owner",
                                 "--password", "Owner-Pass-2026").returncode, 0)
            from app.db import Database
            db = Database(Path(d) / "data" / "pharmacy.db")
            org = db.one("SELECT id FROM organizations")["id"]
            uid = db.one("SELECT id FROM users")["id"]
            br = db.one("SELECT id FROM branches")["id"]
            with db.transaction():
                db.execute("INSERT INTO import_runs (id, org_id, branch_id, kind, source_system, filename, file_sha256,"
                           " mapping, options, status, applied_at) VALUES ('r', ?, ?, 'opening_stock', 'estock', 'x',"
                           " 'h', '{}', '{}', 'applied', '2026-11-21T10:00:00.000Z')", (org, br))
                db.execute("INSERT INTO sales (id, org_id, branch_id, status, sale_no, total, completed_at, created_by)"
                           " VALUES ('s1', ?, ?, 'completed', 7, 1850, '2026-11-21T16:05:00.000Z', ?)", (org, br, uid))
            db.close()
            ok = dict(PHARM_E2E_PHARMACY="صيدليه النور")
            r = run("fresh-start-check", **ok, PHARM_E2E_SALES_YES="YES")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("no. 7", r.stdout)
            self.assertIn("18.50", r.stdout)
            self.assertEqual(run("fresh-start-check", **ok, PHARM_E2E_SALES_YES="NO").returncode, 1)
            self.assertEqual(run("fresh-start-check", **ok).returncode, 1)          # nothing typed: stops
            note = (Path(d) / "fresh-start-check.txt").read_text(encoding="utf-8-sig")
            self.assertIn("صيدلية النور", note)
            self.assertIn("رقم 7", note)


if __name__ == "__main__":
    unittest.main()
