"""RELEASE_RUNBOOK §10 is tonight's launch night (manager 19/11 08:15): the facts and their order."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestLaunchNightRunbook(unittest.TestCase):
    def setUp(self):
        self.sec = (ROOT / "docs" / "RELEASE_RUNBOOK.md").read_text(encoding="utf-8").split("## 10.", 1)[1]

    def test_tools_folder_and_desktop_shortcut(self):
        self.assertIn("«أدوات الصيدلية»", self.sec)
        self.assertIn(r"%LOCALAPPDATA%\PharmacyPlatform\tools", self.sec)
        self.assertIn(r"%LOCALAPPDATA%\PharmacyPlatform\launch-night", self.sec)

    def test_the_night_in_order(self):
        night = self.sec[self.sec.index("### ب."):]
        order = ["اقفل شيفت إي ستوك", "اقفل برنامج إي ستوك", "`estock-db-read.bat`", "`launch-fresh-start.bat`",
                 "`estock-db-import.bat`", "`owner-report.txt`", "`YES`"]
        at = [night.index(x) for x in order]
        self.assertEqual(at, sorted(at), order)
        self.assertIn("نقدية أول شيفت", night)
        self.assertIn("القراية النهائية", night)
        self.assertIn("إي ستوك مقفول", night)

    def test_what_the_owner_finds_after_the_move(self):
        for fact in ("مخزن 2", "hospital-stock.csv", "«0000»", "«مصادر متفرقة (جرد)»", "«الصيدلية»", "حساب داخلي",
                     "estock-archive.sqlite", "تسوية افتتاحية", "openings-confirmed.xlsx"):
            self.assertIn(fact, self.sec, fact)

    def test_owner_confirms_real_balances_after_the_move(self):
        d = self.sec[self.sec.index("### د."):]
        self.assertIn("من البرنامج", d)
        self.assertIn("قبل YES", d)                 # until the screen exists, the file is the way before the move

    def test_the_excel_route_is_only_a_fallback(self):
        self.assertLess(self.sec.index("`estock-db-import.bat`"), self.sec.index("`estock-import.bat`"))
        self.assertIn("احتياطي بس", self.sec)

    def test_codex_362_the_trial_runs_on_an_empty_database_and_the_file_comes_first(self):
        trial = self.sec[self.sec.index("### أ."):self.sec.index("### ب.")]
        self.assertIn("REHEARSAL", trial)
        self.assertIn("قاعدة جديدة فاضية", trial)
        self.assertIn("قبل ما تشغّل `estock-db-import.bat`", trial)
        self.assertIn("وهو مستني YES", trial)


class TestBatRehearsalAndConfirmFile(unittest.TestCase):
    """Codex on #362: estock-db-import.bat runs the rehearsal when the program still holds trial data, and never
    applies when openings-confirmed.xlsx appeared or changed after the dry run."""

    def setUp(self):
        self.L = [l.strip() for l in (ROOT / "scripts/windows/estock-db-import.bat").read_text(encoding="utf-8").splitlines()]

    def test_exit_4_runs_the_rehearsal_without_yes(self):
        dry = next(i for i, l in enumerate(self.L) if '"%MANAGE%" import-estock-db' in l and "-dry" in l)
        to = next(i for i, l in enumerate(self.L) if l.startswith('if "%RC%"=="4"'))
        self.assertTrue(dry < to < dry + 7)
        self.assertIn("goto rehearsal", self.L[to])
        r = self.L.index(":rehearsal")
        block = self.L[r:r + 20]
        run = next(l for l in block if '"%MANAGE%" import-estock-db' in l)
        self.assertIn("--rehearsal", run)
        self.assertNotIn("--apply", run)
        self.assertTrue(any(":owner" in l for l in block))
        self.assertFalse(any("Type YES" in l for l in block))
        self.assertTrue(any("launch-fresh-start.bat" in l for l in block))

    def test_a_confirm_file_that_appeared_or_changed_after_the_dry_run_stops_the_move(self):
        yes = next(i for i, l in enumerate(self.L) if 'set /p "ANS=Type YES' in l)
        apply = next(i for i, l in enumerate(self.L) if '"%MANAGE%" import-estock-db' in l and "--apply" in l)
        guard = [l for l in self.L[yes:apply] if "openings-confirmed.xlsx" in l and "goto fail" in l]
        self.assertTrue(guard, self.L[yes:apply])
        self.assertTrue(any("CONFSTAMP" in l for l in self.L[yes:apply]))
        start = next(i for i, l in enumerate(self.L) if "--confirm" in l and "EXTRA" in l)
        self.assertTrue(any("CONFSTAMP" in l for l in self.L[start - 2:start + 3]))
