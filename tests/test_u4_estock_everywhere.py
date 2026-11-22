"""E-Stock history everywhere (owner 22/11 17:39: «كل مبيعات وفواتير إي ستوك القديمة … تدخل في القوائم بتاعتنا كأن
البرنامج شغال بقاله سنتين … بدون أي غلط»). The history is read-only and tagged «من إي ستوك»; it must never be posted
again into stock, drawer, treasury, balances, reports or claims. Synthetic data only.

The proof: every table of the program's database is hashed, and the program's own reports are read, before and
after every history view is opened; both must be identical."""
import hashlib
from datetime import date, timedelta

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_estock_history_api import ApiBase

VOLATILE = {"sessions", "idempotency_keys", "usage_counters", "schema_migrations"}
RANGE = "from=2020-01-01&to=2030-12-31"


class EverywhereBase(ApiBase):
    def setUp(self):
        super().setUp()
        r = self.c.put("/api/settings/hikma-claim", json={"branch": "shibin", "customer_id": self.cust},
                       headers={"X-Requested-With": "pharm"})
        self.assertEqual(r.status_code, 200, r.text)            # the claim is one of the totals compared

    def db_tables(self) -> dict:
        out = {}
        for t in sorted(r["name"] for r in self.db.all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")):
            if t in VOLATILE or t.endswith("_fts") or "_fts_" in t:
                continue
            out[t] = hashlib.sha256(repr([tuple(r) for r in self.db.all(f'SELECT * FROM "{t}" ORDER BY 1')]).encode()).hexdigest()
        return out

    def db_hash(self) -> str:
        h = hashlib.sha256()
        for t in sorted(r["name"] for r in self.db.all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")):
            if t in VOLATILE or t.endswith("_fts") or "_fts_" in t:
                continue
            for r in self.db.all(f'SELECT * FROM "{t}" ORDER BY 1'):
                h.update(repr((t, tuple(r))).encode())
        return h.hexdigest()

    def our_reports(self) -> dict:
        """The program's own reports over the last year (their limit), each must answer 200."""
        f, t = (date.today() - timedelta(days=360)).isoformat(), date.today().isoformat()
        q = (date.today() - timedelta(days=85)).isoformat()             # the claim takes up to 3 months
        out = {}
        for url in (f"/api/reports/invoices?from={f}T00:00&to={t}T23:59", f"/api/reports/sales?from={f}&to={t}",
                    f"/api/reports/profit?from={f}&to={t}", "/api/reports/stock",
                    f"/api/reports/hikma-claim?branch=shibin&from={q}&to={t}", "/api/suppliers/all", "/api/customers",
                    f"/api/estock/statement/customer/{self.cust}", f"/api/estock/statement/supplier/{self.sup}"):
            r = self.c.get(url, headers={"X-Requested-With": "pharm"})
            self.assertEqual(r.status_code, 200, (url, r.text[:200]))
            out[url] = r.text
        return out

    def every_history_view(self):
        item = self.item("100")["id"]
        for kind in ("sale", "sale_return", "purchase"):
            rows = self.get(f"/api/estock/invoices?kind={kind}&{RANGE}")["invoices"]
            for r in rows[:3]:
                self.get(f"/api/estock/invoices/{kind}/{r['estock_id']}")
        self.get(f"/api/estock/invoices?kind=sale&party={self.cust}")
        self.get(f"/api/estock/invoices?kind=purchase&party={self.sup}")
        self.get(f"/api/estock/invoices?kind=sale&item={item}")
        self.get(f"/api/estock/items/{item}/history")
        self.get(f"/api/estock/customers/{self.cust}/items")
        self.get(f"/api/estock/statement/customer/{self.cust}")
        self.get(f"/api/estock/statement/supplier/{self.sup}")
        self.get(f"/api/estock/period?{RANGE}&by=month")
        self.get(f"/api/estock/period?{RANGE}&by=day")


class TestNothingChanges(EverywhereBase):
    def test_every_table_and_every_report_identical_after_the_history_is_read(self):
        before_db, before_rep = self.db_hash(), self.our_reports()
        self.every_history_view()
        self.every_history_view()                       # twice: nothing accumulates
        self.assertEqual(self.db_hash(), before_db)
        self.assertEqual(self.our_reports(), before_rep)


class TestPeriod(EverywhereBase):
    def test_period_totals_are_e_stock_figures_tagged(self):
        out = self.get(f"/api/estock/period?{RANGE}&by=month")
        self.assertTrue(out["rows"])
        self.assertTrue(all(r["source"] == "estock" for r in out["rows"]))
        self.assertEqual(out["source"], "estock")
        tot = out["total"]
        self.assertEqual(tot["sales_count"], sum(r.get("sales_count", 0) for r in out["rows"]))
        self.get(f"/api/estock/period?{RANGE}&by=year", 422)

    def test_period_needs_sales_totals(self):
        from app.core import people
        from tests.test_estock_history_api import PW
        people.add_user(self.db, self.ctx, username="cashier2", full_name="C", password=PW, role_code="assistant")
        self.login_as("cashier2", PW)
        self.get(f"/api/estock/period?{RANGE}", 403)


INLINE = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, cust, item] = process.argv.slice(1);
""" + LOGIN + r"""
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));
  const out = {};
  await p.goto(base + '/#/customers/' + cust);
  await p.waitForSelector('#esInline tr[data-go]');
  out.cust = await p.$$eval('#esInline tr[data-go]', (t) => t.map((x) => x.innerText.includes('من إي ستوك')));
  await p.goto(base + '/#/items/' + item);
  await p.waitForFunction(() => location.hash.startsWith('#/items/')
    && (document.querySelector('#esInline th:nth-child(2)') || {}).textContent === 'النوع');
  out.item = await p.$$eval('#esInline tr[data-go]', (t) => t.length);
  await p.goto(base + '/#/sales');
  await p.waitForSelector('#iF');
  await p.fill('#iF', '2026-11-01T00:00');
  await p.fill('#iT', '2026-11-20T23:59');
  await p.click('#iGo');
  await p.waitForSelector('#esBelow tr[data-go]');
  out.below = await p.$$eval('#esBelow tr[data-go]', (t) => t.map((x) => x.innerText.includes('من إي ستوك')));
  out.belowText = await p.textContent('#esBelow');
  console.log(JSON.stringify({ ...out, errs }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestInlineScreens(EverywhereBase):
    def test_customer_item_and_sales_list_show_the_history_tagged(self):
        before = self.db_tables()
        n_audit = self.db.one("SELECT count(*) n FROM audit_log")["n"]
        out = run_node(self, INLINE, "owner", OWNER_PW, self.cust, self.item("100")["id"])
        self.assertTrue(out["cust"] and all(out["cust"]))
        self.assertGreater(out["item"], 0)
        self.assertTrue(out["below"] and all(out["below"]))
        self.assertIn("البرنامج + إي ستوك", out["belowText"])
        # the program sold nothing in the window: the combined figure is E-Stock's sales before returns, exactly
        es = self.get("/api/estock/period?from=2026-11-01&to=2026-11-20&by=month")["total"]["sales"]
        self.assertIn(f"البرنامج + إي ستوك: {es} ج", out["belowText"])
        self.assertEqual(out["errs"], [])
        after = self.db_tables()
        # the screens wrote nothing; the browser's own login is the one audit entry allowed
        self.assertEqual({t for t in after if after[t] != before.get(t)} - {"audit_log"}, set())
        new = [r["action"] for r in self.db.all("SELECT action FROM audit_log ORDER BY rowid LIMIT -1 OFFSET ?", (n_audit,))]
        self.assertEqual(set(new), {"auth.login"})


class TestReviewFixes(EverywhereBase):
    def test_unreadable_totals_are_counted_not_turned_into_zero(self):
        import sqlite3
        from contextlib import closing
        from app.dataimport import estock_history as eh
        p = eh.archive_path(self.db)
        with closing(sqlite3.connect(p)) as con:
            col = [r[1] for r in con.execute('PRAGMA table_info("e_Sales_header")') if r[1].lower() == "total_bill_net"][0]
            con.execute(f'UPDATE "e_Sales_header" SET "{col}"=\'garbage\' WHERE rowid=(SELECT min(rowid) FROM "e_Sales_header")')
            con.commit()
        out = self.get(f"/api/estock/period?{RANGE}&by=month")
        self.assertEqual(out["total"]["sales_unreadable"], 1)

    def test_no_purchases_without_purchases_read(self):
        from app.core import people
        from tests.test_estock_history_api import PW
        uid = people.add_user(self.db, self.ctx, username="mgr2", full_name="M", password=PW, role_code="manager")
        people.set_exception(self.db, self.ctx, uid if isinstance(uid, str) else uid["id"], "purchases.read", "deny")
        self.login_as("mgr2", PW)
        out = self.get(f"/api/estock/period?{RANGE}&by=month")
        self.assertNotIn("purchases", out["total"])
        self.assertTrue(all("purchases" not in r for r in out["rows"]))

    def test_the_screen_guards(self):
        from pathlib import Path
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        i = js.index("async function estockSalesBelow")
        body = js[i:js.index("\n}\n", i)]
        self.assertIn("ES_SEQ", body)                         # a late answer never lands under a newer search
        self.assertIn("unreadable", body)
        at = js.index("estockSalesBelow($(\"#iBox\")")
        call = js[at - 600:at + 300]
        self.assertIn("R.truncated", call)                    # a capped program total is never added up
        self.assertIn("00:00", call)                          # part of a day: E-Stock counts whole days only


class TestCodexFixes(EverywhereBase):
    def test_period_total_is_rounded_once_over_exact_values(self):
        from tests.test_estock_db_import import Base, base_tables
        self.db.close()
        Base.setUp(self)
        t = base_tables()
        t["Sales_header"] = [{"sales_id": "1", "customer_id": "1", "total_bill_net": "0.004", "insert_date": "2026-10-01T10:00:00"},
                             {"sales_id": "2", "customer_id": "1", "total_bill_net": "0.004", "insert_date": "2026-11-02T10:00:00"}]
        self.assertTrue(self.run_import(tables=t)["ok"])
        from app.dataimport import estock_history as eh
        rows, tot = eh.period_with_total(self.db, self.ctx, "2026-10-01", "2026-11-30", by="month")
        self.assertEqual([r["sales"] for r in rows], ["0.00", "0.00"])
        self.assertEqual(tot["sales"], "0.01")                   # 0.008 rounded once, not 0.00 + 0.00

    def test_screen_invalidates_and_print_selected_leaves_history_out(self):
        from pathlib import Path
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        at = js.index("estockSalesBelow($(\"#iBox\")")
        self.assertIn("ES_SEQ++", js[at:at + 500])              # hiding the section drops a pending answer too
        p = js.index("const printInv")
        self.assertIn('copy.querySelector("#esBelow")?.remove()', js[p:p + 1500])


class TestCodexFixes2(EverywhereBase):
    def test_e_stock_side_follows_the_answered_request(self):
        from pathlib import Path
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        at = js.index("estockSalesBelow($(\"#iBox\")")
        near = js[at - 400:at + 200]
        self.assertIn("R.asked", near)
        self.assertNotIn('$("#iF").value', near)
