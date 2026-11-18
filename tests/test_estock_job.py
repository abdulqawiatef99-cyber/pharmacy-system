"""End-to-end migration job on synthetic E-Stock exports (layout of the real ones)."""
import json
import tempfile
from pathlib import Path

from app.dataimport import estock_job
from app.core.errors import ValidationFailed
from tests.helpers import DBTestCase
from tests.test_estock import CATALOG, stock_rows, xlsx


class TestJob(DBTestCase):
    def files(self, packs):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "c.xlsx").write_bytes(xlsx(CATALOG))
        (d / "s.xlsx").write_bytes(xlsx(stock_rows()))
        (d / "p.json").write_text(json.dumps({"packs": packs}), encoding="utf-8")
        return d

    def test_unknown_pack_is_listed_not_guessed(self):
        d = self.files({})
        rep = estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                             packs_file=d / "p.json", stores=["الصيدلية"])
        self.assertTrue(rep["stock"]["reconciliation"]["ok"])
        self.assertEqual([x["code"] for x in rep["stock"]["skipped_unknown_pack"]], ["30906"])
        self.assertEqual(rep["stock"]["applied"]["stock_rows"], 1)

    def test_reviewed_pack_file_completes_migration(self):
        d = self.files({"30906": 3})
        rep = estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                             packs_file=d / "p.json", stores=["الصيدلية"])
        self.assertEqual(rep["stock"]["applied"]["stock_rows"], 2)
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2)
        self.assertFalse(rep["stock"].get("skipped_unknown_pack"))


class TestPacksFile(DBTestCase):
    """The reviewed packs sheet we send the owner comes back as xlsx: read it exactly or refuse it."""

    def packs(self, rows):
        p = Path(tempfile.mkdtemp(dir=self.tmp)) / "packs.xlsx"
        p.write_bytes(xlsx(rows))
        return estock_job.read_packs(p)

    def test_decision_column(self):
        self.assertEqual(self.packs([["الكود", "الاسم", "القرار"], [30906, "ليثيومود", 3], ["77", "جل", "1"]]),
                         {"30906": 3, "77": 1})

    def test_units_column_when_there_is_no_decision_column(self):
        self.assertEqual(self.packs([["الكود", "عدد الوحدات المقترح"], [30906, 3]]), {"30906": 3})

    def test_the_decision_wins_over_the_suggestion(self):
        self.assertEqual(self.packs([["الكود", "عدد الوحدات المقترح", "القرار"], [30906, 2, 3]]), {"30906": 3})

    def test_codes_read_as_numbers_keep_their_text(self):
        self.assertEqual(self.packs([["الكود", "القرار"], [30906.0, 3.0], [1, 2]]), {"30906": 3, "1": 2})

    def test_blank_rows_and_undecided_items_are_left_out(self):
        self.assertEqual(self.packs([["الكود", "القرار"], [None, 3], [76, None], [76, ""], [77, 2], [78]]), {"77": 2})

    def test_a_fraction_or_zero_is_refused_naming_the_item(self):
        for bad in (2.5, 0, -1, "1.5"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationFailed) as e:
                    self.packs([["الكود", "القرار"], [77, 2], [30906, bad]])
                self.assertIn("30906", str(e.exception))

    def test_a_sheet_without_the_columns_is_refused(self):
        for head in (["كود", "القرار"], ["الكود", "الكمية"]):
            with self.subTest(head=head):
                with self.assertRaises(ValidationFailed):
                    self.packs([head, [77, 2]])

    def test_json_packs_skip_zero_entries(self):
        p = Path(tempfile.mkdtemp(dir=self.tmp)) / "p.json"
        p.write_text(json.dumps({"30906": 3, "77": 0}), encoding="utf-8")
        self.assertEqual(estock_job.read_packs(p), {"30906": 3})

    def test_sheet_refuses_text_and_huge_numbers_quickly(self):
        # "1e10000000" passed the whole-number check and int() spent seconds building it; "x" crashed (Decimal)
        import time
        for bad in ("1e10000000", "x", "NaN", "Infinity", 0, -2, 2.5):
            with self.subTest(bad=bad):
                t = time.monotonic()
                with self.assertRaises(ValidationFailed) as e:
                    self.packs([["الكود", "الاسم", "القرار"], [30906, "ليثيومود", bad]])
                self.assertIn("30906", str(e.exception))
                self.assertLess(time.monotonic() - t, 1)

    def test_json_packs_refuse_a_fraction_like_the_sheet(self):
        for bad in (2.5, "2.5", -3, True, "x"):
            with self.subTest(bad=bad):
                p = Path(tempfile.mkdtemp(dir=self.tmp)) / "p.json"
                p.write_text(json.dumps({"packs": {"77": 2, "30906": bad}}), encoding="utf-8")
                with self.assertRaises(ValidationFailed) as e:
                    estock_job.read_packs(p)
                self.assertIn("30906", str(e.exception))


class TestJobRules(DBTestCase):
    def files(self, packs_rows=None, store2_cost=20.0):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "c.xlsx").write_bytes(xlsx(CATALOG))
        rows = stock_rows()
        if not store2_cost:                       # a free box (bonus) in the second store, totals to match
            rows[6][2] = rows[6][4] = 0
            rows[7][2] = 0
            rows[8][3] = 1676.1343
        (d / "s.xlsx").write_bytes(xlsx(rows))
        (d / "p.xlsx").write_bytes(xlsx(packs_rows or [["الكود", "القرار"], [30906, 3]]))
        return d

    def run_job(self, d, **kw):
        return estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                              packs_file=d / "p.xlsx", **kw)

    def test_xlsx_packs_file_sets_the_pack_and_lists_codes_not_in_the_catalog(self):
        d = self.files([["الكود", "القرار"], [30906, 3], [99999, 5]])
        rep = self.run_job(d, stores=["الصيدلية"])
        self.assertEqual(rep["packs"], {"applied": 1, "codes_not_in_catalog": ["99999"]})
        item = self.db.one("SELECT pack_size FROM items WHERE org_id=? AND code='30906'", (self.ctx.org_id,))
        self.assertEqual(item["pack_size"], 3)
        self.assertEqual(rep["stock"]["applied"]["stock_rows"], 2)
        self.assertEqual(rep["rules"]["stores"], ["الصيدلية"])

    def test_zero_cost_stock_is_skipped_and_listed_by_default(self):
        rep = self.run_job(self.files(store2_cost=0), stores=["الصيدلية", "مستشفيات"])
        self.assertEqual([x["code"] for x in rep["stock"]["skipped_zero_cost"]], ["1"])
        self.assertEqual(rep["stock"]["posted_zero_cost"], [])
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2)       # the free box is not posted

    def test_zero_cost_stock_is_posted_at_cost_zero_only_with_the_rule(self):
        rep = self.run_job(self.files(store2_cost=0), stores=["الصيدلية", "مستشفيات"], accept_zero_cost=True)
        self.assertEqual([x["code"] for x in rep["stock"]["posted_zero_cost"]], ["1"])
        self.assertFalse(rep["stock"].get("skipped_zero_cost"))
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2 + 1)   # + the free box (pack of 1)
        self.assertTrue(rep["rules"]["accept_zero_cost"])
        self.assertTrue(rep["stock"]["reconciliation"]["ok"])


class TestJobRerun(DBTestCase):
    """`manage.py import-estock --apply` after a failure in a later step: the catalog (step 1) is already
    in, and its file is refused as "already imported"; the job must go on from there (R4 review)."""

    def files(self, stock):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "c.xlsx").write_bytes(xlsx(CATALOG))
        (d / "s.xlsx").write_bytes(xlsx(stock))
        (d / "p.json").write_text(json.dumps({"packs": {"30906": 3}}), encoding="utf-8")
        return d

    def run_job(self, d):
        return estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                              packs_file=d / "p.json", stores=["الصيدلية"])

    def counts(self):
        return (self.db.one("SELECT count(*) n FROM items WHERE org_id=?", (self.ctx.org_id,))["n"],
                self.db.one("SELECT count(*) n FROM stock_movements WHERE org_id=?", (self.ctx.org_id,))["n"])

    def test_rerun_after_the_stock_step_failed_skips_the_applied_catalog(self):
        from unittest import mock
        from app.core.errors import Conflict
        from app.dataimport.service import DataImport
        d = self.files(stock_rows())
        real, calls = DataImport.apply, []

        def fail_on_stock(di, ctx, run_id):
            calls.append(run_id)
            if len(calls) == 2:                       # step 3: the opening stock
                raise Conflict("الأرصدة بعد الاستيراد لا تطابق الملف", code="reconciliation_failed")
            return real(di, ctx, run_id)
        with mock.patch.object(DataImport, "apply", fail_on_stock):
            with self.assertRaises(Conflict):
                self.run_job(d)
        items, moves = self.counts()
        self.assertGreater(items, 0)                  # the catalog stayed
        self.assertEqual(moves, 0)                    # no stock was posted
        rep = self.run_job(d)                         # the same command again
        self.assertTrue(rep["catalog"]["already_applied"])
        self.assertEqual(rep["catalog"]["created"], items)            # the first run's figures, for the summary
        self.assertIn("updated", rep["catalog"])
        self.assertIn("kept_duplicates", rep["catalog"])
        self.assertTrue(rep["stock"]["reconciliation"]["ok"])
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2)
        self.assertEqual(self.counts()[0], items)     # no item was added twice

    def test_a_first_run_reports_the_catalog_as_before(self):
        rep = self.run_job(self.files(stock_rows()))
        self.assertNotIn("already_applied", rep["catalog"])
        self.assertGreater(rep["catalog"]["created"], 0)

    def test_a_complete_run_is_still_refused_the_second_time(self):
        from app.core.errors import Conflict
        d = self.files(stock_rows())
        self.run_job(d)
        before = self.counts()
        with self.assertRaises(Conflict) as e:        # the stock file was applied: never twice
            self.run_job(d)
        self.assertEqual(e.exception.code, "already_applied")
        self.assertEqual(self.counts(), before)
