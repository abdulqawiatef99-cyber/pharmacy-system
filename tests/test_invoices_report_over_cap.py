"""#461: the invoices report adds up EVERY matching invoice of the period, not only the rows it lists, and
its pages are stable, so an export that walks the pages holds every match once. Synthetic data only."""
from datetime import date
from unittest import mock

from app.core import people
from app.db import new_id
from app.reports import invoices
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

D = date.today().isoformat()
PW = "Staff-Pass-2026"


class Seeded(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)

    def rep(self, **kw):
        return invoices.report(self.db, self.ctx, date_from=D, date_to=D, **kw)

    def pages(self, **kw):
        out, offset = [], 0
        while True:
            r = self.rep(offset=offset, **kw)
            out += r["rows"]
            offset += len(r["rows"])
            if not r["truncated"]:
                return out, r


class TestTotalsCoverEveryMatch(Seeded):
    def setUp(self):
        super().setUp()
        uid = people.add_user(self.db, self.ctx, username="cashier2", full_name="Second", password=PW, role_code="manager")
        self.c2 = self.login("cashier2", PW)
        self.uid2 = uid
        self.sales = []
        for k in range(11):                                   # 11 invoices of 1 unit = 20.00 each, cost 15.00
            ctx = self.c2 if k % 3 == 0 else self.ctx         # 4 by the second cashier
            card = 2000 if k % 2 else 0                       # 5 by card, 6 cash
            self.sales.append(self.sell(ctx=ctx, units=1, card=card))
        self.s.return_items(self.ctx, self.sales[0]["id"], {self.sales[0]["lines"][0]["id"]: 1},
                            refund_method="cash", reason="اختبار")
        patch = mock.patch.object(invoices, "MAX_ROWS", 4)
        patch.start()
        self.addCleanup(patch.stop)

    def test_period_totals_are_complete_above_the_cap(self):
        r = self.rep()
        self.assertEqual(len(r["rows"]), 4)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["total_rows"], 11)
        t = r["totals"]
        self.assertEqual(t["n"], 11)
        self.assertEqual((t["subtotal"], t["total"], t["returned_total"], t["net"]), (22000, 22000, 2000, 20000))
        self.assertEqual((t["paid_cash"], t["paid_card"]), (12000, 10000))
        self.assertEqual(t["returned_n"], 1)
        self.assertEqual(t["profit"], 10 * 500)               # 20.00 - 15.00 each; one invoice fully returned

    def test_totals_follow_the_cashier_and_tender_filters(self):
        r = self.rep(user_id=self.uid2)
        self.assertEqual((r["total_rows"], r["totals"]["n"], r["totals"]["total"]), (4, 4, 8000))
        r = self.rep(types=["card"])
        self.assertEqual((r["total_rows"], r["totals"]["total"], r["totals"]["paid_card"]), (5, 10000, 10000))
        r = self.rep(types=["cash"])
        self.assertEqual((r["total_rows"], r["totals"]["total"]), (6, 12000))

    def test_pages_are_stable_and_hold_every_match_once(self):
        rows, last = self.pages(with_lines=True)
        self.assertEqual(len(rows), 11)
        self.assertEqual({x["id"] for x in rows}, {x["id"] for x in self.sales})
        self.assertEqual(sum(x["total"] for x in rows), last["totals"]["total"])      # the export adds up to the totals
        self.assertEqual(sum(x["profit"] for x in rows), last["totals"]["profit"])
        again, _ = self.pages(with_lines=True)
        self.assertEqual([x["id"] for x in rows], [x["id"] for x in again])
        self.assertTrue(all(x["items"] for x in rows))

    def test_every_page_carries_the_same_complete_totals(self):
        first, second = self.rep(), self.rep(offset=4)
        self.assertEqual(first["totals"], second["totals"])
        self.assertEqual(second["offset"], 4)
        self.assertEqual(self.rep(offset=8)["truncated"], False)
        self.assertEqual(self.rep(offset=50)["rows"], [])

    def test_a_page_asked_without_totals_skips_the_adding_up(self):
        r = self.rep(offset=4, totals=False)
        self.assertEqual(len(r["rows"]), 4)
        self.assertIsNone(r["totals"])
        self.assertEqual(r["total_rows"], 11)

    def test_bad_paging_is_refused(self):
        from app.core.errors import ValidationFailed
        with self.assertRaises(ValidationFailed):
            self.rep(offset=-1)


class TestThreeThousandAndOneInvoices(Seeded):
    """The audit's reproduction: 3,001 invoices of 1.00 EGP on one day gave 3,000.00 and a count of 3,000."""

    def test_the_real_cap(self):
        one = self.sell(units=2)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET subtotal=100, total=100, paid_cash=100 WHERE id=?", (one["id"],))
        cols = [c["name"] for c in self.db.all("PRAGMA table_info(sales)") if c["name"] not in ("id", "sale_no")]
        with self.db.transaction():
            for k in range(3000):                              # header copies: the totals columns are what counts
                self.db.execute(f"INSERT INTO sales (id, sale_no, {', '.join(cols)}) SELECT ?, ?, {', '.join(cols)}"
                                " FROM sales WHERE id=?", (new_id(), one["sale_no"] + 1 + k, one["id"]))
        r = self.rep()
        self.assertEqual(len(r["rows"]), 3000)
        self.assertTrue(r["truncated"])
        self.assertEqual((r["total_rows"], r["totals"]["n"]), (3001, 3001))
        self.assertEqual((r["totals"]["total"], r["totals"]["paid_cash"], r["totals"]["net"]), (300100, 300100, 300100))
        rows, _ = self.pages()
        self.assertEqual(len({x["id"] for x in rows}), 3001)


class TestRoute(APICase):
    def test_paging_parameters(self):
        self.http_login()
        base = f"/api/reports/invoices?from={D}T00:00&to={D}T23:59"
        r = self.c.get(base, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["offset"], r.json()["total_rows"], r.json()["page_size"]), (0, 0, invoices.MAX_ROWS))
        r = self.c.get(base + "&offset=3000&totals=0", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["offset"], r.json()["totals"]), (3000, None))
        for bad in ("-1", "x", "1.5", "9999999999"):
            self.assertEqual(self.c.get(base + f"&offset={bad}", headers=H).status_code, 422, bad)
