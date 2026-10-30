"""#72: a pack split over two batches was priced per batch by the release (2 x 33.33 + 33.33 = 99.99).
Returns, the invoices report and the discount report must keep that recorded value after the upgrade:
never refund or report the 100.00 the current rule would charge for the same pack today.

Cross-version: the sales are written by main's own code (tests/fixtures/make_legacy_split_sale.py,
CI sets PHARM_RELEASE_SRC). The rule itself is also covered without a release checkout below.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import accounts
from app.db import Database
from app.reports import invoices
from app.sales import service as sales
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import OWNER_PW

MAKER = Path(__file__).parent / "fixtures" / "make_legacy_split_sale.py"
PACK = {"unit_price": 3333, "pack_price": 10000, "pack_size": 3}


class TestRecordedBasis(unittest.TestCase):
    """The pricing rule a line was sold with is the one its returns use."""

    def allocs(self):
        return [{"id": "a", "qty_base": 2, **PACK}, {"id": "b", "qty_base": 1, **PACK}]

    def test_legacy_line_keeps_per_batch_values(self):
        al = self.allocs()
        full = {"a": 2, "b": 1}
        self.assertEqual(sales.kept_value(al, full, 0, 9999), 9999)
        self.assertEqual(sales.kept_value(al, {"a": 2, "b": 0}, 0, 9999), 6666)
        self.assertEqual(sales.kept_value(al, {"a": 0, "b": 0}, 0, 9999), 0)
        self.assertEqual(sales.line_gross_as_sold(al, 0, 9999), 9999)

    def test_current_line_unchanged(self):
        al = self.allocs()
        self.assertEqual(sales.kept_value(al, {"a": 2, "b": 1}, 0, 10000), 10000)
        self.assertEqual(sales.kept_value(al, {"a": 2, "b": 0}, 0, 10000), 6666)
        self.assertEqual(sales.line_gross_as_sold(al, 0, 10000), 10000)
        self.assertEqual(sales.kept_value(al, {"a": 2, "b": 1}, 0), 10000)          # no record: current rule

    def test_discounted_legacy_line(self):
        al = self.allocs()                    # 10% off 99.99 = 9999 - 1000 = 8999 under the release
        self.assertEqual(sales.kept_value(al, {"a": 2, "b": 1}, 1000, 8999), 8999)
        self.assertEqual(sales.line_gross_as_sold(al, 1000, 8999), 9999)

    def test_refunds_never_exceed_the_recorded_value(self):
        al = self.allocs()                    # a record neither rule reproduces: still capped and exact
        self.assertEqual(sales.kept_value(al, {"a": 2, "b": 1}, 0, 9990), 9990)
        self.assertLessEqual(sales.kept_value(al, {"a": 2, "b": 0}, 0, 5000), 5000)


class TestLegacySplitSales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = os.environ.get("PHARM_RELEASE_SRC")
        if not src or not (Path(src) / "app").is_dir():
            raise unittest.SkipTest("release checkout not available (CI sets PHARM_RELEASE_SRC)")
        cls.out = Path(tempfile.mkdtemp(prefix="pharm-split-"))
        env = {**os.environ, "PYTHONPATH": src, "PHARM_DATA_DIR": str(cls.out / "data")}
        r = subprocess.run([sys.executable, str(MAKER), str(cls.out / "release")], cwd=src, env=env,
                           capture_output=True, text=True, timeout=120, check=False)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError("release code could not write the sales:\n" + r.stderr[-2000:])
        cls.src_db = Path(r.stdout.strip().splitlines()[-1])
        cls.ids = json.loads((cls.src_db.parent / "legacy-split.json").read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-split-db-"))
        shutil.copy(self.src_db, self.tmp / "pharmacy.db")
        self.db = Database(self.tmp / "pharmacy.db")
        self.db.migrate()
        self.ctx = accounts.resolve_session(self.db, accounts.login(self.db, "owner", OWNER_PW)["token"])
        self.s = Sales(self.db)
        self.line = self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (self.ids["cash"],))["id"]

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sale(self, key="cash"):
        return self.db.one("SELECT total, returned_total FROM sales WHERE id=?", (self.ids[key],))

    def test_release_recorded_99_99(self):
        self.assertEqual(self.sale()["total"], 9999)
        self.assertEqual(self.sale("credit")["total"], 9999)

    def test_full_cash_return_refunds_what_was_paid(self):
        r = self.s.return_items(self.ctx, self.ids["cash"], {self.line: 3}, refund_method="cash", reason="synthetic return")
        self.assertEqual(r["refund"], 9999)
        self.assertEqual(self.sale()["returned_total"], 9999)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 9999 - 9999)   # opened with 0

    def test_partial_returns_add_up_to_what_was_paid(self):
        r1 = self.s.return_items(self.ctx, self.ids["cash"], {self.line: 1}, refund_method="cash", reason="synthetic return")
        r2 = self.s.return_items(self.ctx, self.ids["cash"], {self.line: 2}, refund_method="cash", reason="synthetic return")
        self.assertEqual((r1["refund"], r2["refund"]), (3333, 6666))
        self.assertEqual(self.sale()["returned_total"], 9999)
        self.assertEqual(CashDrawer(self.db).summary(self.ctx)["expected_cash"], 0)

    def test_credit_return_credits_what_was_charged(self):
        line = self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (self.ids["credit"],))["id"]
        r = self.s.return_items(self.ctx, self.ids["credit"], {line: 3}, refund_method="credit", reason="synthetic return")
        self.assertEqual(r["refund"], 9999)
        cust = self.db.one("SELECT customer_id FROM sales WHERE id=?", (self.ids["credit"],))["customer_id"]
        self.assertEqual(self.db.one("SELECT sum(amount) n FROM customer_ledger WHERE org_id=? AND customer_id=?",
                                     (self.ctx.org_id, cust))["n"], 0)

    def test_reports_keep_the_recorded_value(self):
        d = sales.local_today().isoformat()   # the report works in local days (#52)
        rows = {r["id"]: r for r in invoices.report(self.db, self.ctx, date_from=d, date_to=d, with_lines=True)["rows"]}
        r = rows[self.ids["cash"]]
        self.assertEqual((r["item_discount"], r["items"][0]["gross"], r["items"][0]["net"]), (0, 9999, 9999))
        self.assertEqual(r["profit"], 9999 - 3 * 2000)              # cost 20.00 per unit

    def test_history_untouched(self):
        before = self.db.all("SELECT id, total, paid_cash, on_credit, returned_total FROM sales ORDER BY id")
        lines = self.db.all("SELECT id, line_total FROM sale_lines ORDER BY id")
        self.s.return_items(self.ctx, self.ids["cash"], {self.line: 1}, refund_method="cash", reason="synthetic return")
        after = self.db.all("SELECT id, total, paid_cash, on_credit FROM sales ORDER BY id")
        self.assertEqual([tuple(r)[:4] for r in before], [tuple(r) for r in after])
        self.assertEqual([tuple(r) for r in lines], [tuple(r) for r in self.db.all("SELECT id, line_total FROM sale_lines ORDER BY id")])
