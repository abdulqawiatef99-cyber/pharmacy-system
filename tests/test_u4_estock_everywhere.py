"""E-Stock history everywhere (owner 22/11 17:39: «كل مبيعات وفواتير إي ستوك القديمة … تدخل في القوائم بتاعتنا كأن
البرنامج شغال بقاله سنتين … بدون أي غلط»). The history is read-only and tagged «من إي ستوك»; it must never be posted
again into stock, drawer, treasury, balances, reports or claims. Synthetic data only.

The proof: every table of the program's database is hashed, and the program's own reports are read, before and
after every history view is opened; both must be identical."""
import hashlib
import json
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
            if "/reports/invoices" in url:                               # `as_of` is the clock of the answer
                body = r.json()
                body.pop("as_of")
                out[url] = json.dumps(body, sort_keys=True, ensure_ascii=False)
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
        import os
        import stat
        mode = os.stat(p).st_mode
        os.chmod(p, mode | stat.S_IWUSR)              # the archive is kept read-only; this test breaks one total on purpose
        with closing(sqlite3.connect(p)) as con:
            col = [r[1] for r in con.execute('PRAGMA table_info("e_Sales_header")') if r[1].lower() == "total_bill_net"][0]
            con.execute(f'UPDATE "e_Sales_header" SET "{col}"=\'garbage\' WHERE rowid=(SELECT min(rowid) FROM "e_Sales_header")')
            con.commit()
        os.chmod(p, mode)                             # read-only again before the program reads it
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
        # since #461 the program total is the whole period's whatever page is listed; only an item filter
        # (E-Stock has no such total) leaves it out of the sum
        self.assertIn("A.item ? null : T.total", call)
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


# ------------------------------------------------------------------ step 2: reports, purchases list, customer page, claim
H2 = {"X-Requested-With": "pharm"}


def _step2_tables():
    from tests.test_estock_returns import tables
    t = tables()
    t["Purchase_header"][0].update({"insert_date": "2026-10-05T09:00:00.000", "bill_number": "P-9"})
    return t


class Step2Base(EverywhereBase):
    TABLES = staticmethod(_step2_tables)                 # an old purchase inside the windows used below


class TestStep2Api(Step2Base):
    def test_one_suppliers_purchases_only(self):
        out = self.get(f"/api/estock/period?{RANGE}&by=month&supplier={self.sup}")
        rows = self.get(f"/api/estock/invoices?kind=purchase&party={self.sup}&{RANGE}")["invoices"]
        self.assertTrue(rows)
        self.assertEqual(out["total"]["purchases_count"], len(rows))
        self.assertNotIn("sales", out["total"])                          # a supplier filter never brings sales in
        self.assertTrue(all("sales" not in r and "returns" not in r for r in out["rows"]))

    def test_a_supplier_e_stock_never_had_gives_nothing_not_everybody(self):
        r = self.c.post("/api/suppliers", json={"name": "مورد جديد خالص"}, headers={**H2, "Idempotency-Key": "s-new"})
        self.assertIn(r.status_code, (200, 201), r.text)
        new = self.db.one("SELECT id FROM suppliers WHERE name='مورد جديد خالص'")["id"]
        out = self.get(f"/api/estock/period?{RANGE}&by=month&supplier={new}")
        self.assertNotIn("purchases", out["total"])
        self.assertEqual(out["rows"], [])

    def test_supplier_filter_needs_purchases_read(self):
        from app.core import people
        from tests.test_estock_history_api import PW
        uid = people.add_user(self.db, self.ctx, username="mgr3", full_name="M", password=PW, role_code="manager")
        people.set_exception(self.db, self.ctx, uid if isinstance(uid, str) else uid["id"], "purchases.read", "deny")
        self.login_as("mgr3", PW)
        self.get(f"/api/estock/period?{RANGE}&supplier={self.sup}", 403)

    def test_hikma_claim_reads_only_the_programs_sales(self):
        # E-Stock sold to the branch customer inside the window; the claim must not see any of it
        es = self.get(f"/api/estock/invoices?kind=sale&party={self.cust}&from=2026-11-01&to=2026-11-20")["invoices"]
        self.assertTrue(es)
        claim = self.get("/api/reports/hikma-claim?branch=shibin&from=2026-11-01&to=2026-11-20")
        self.assertEqual(claim["invoices"], 0)
        self.assertEqual(claim["pharmacy"]["value"] + claim["hospital"]["value"], 0)

    def test_nothing_changes_with_the_step_2_views(self):
        before_db, before_rep = self.db_hash(), self.our_reports()
        for _ in range(2):
            self.get(f"/api/estock/period?{RANGE}&by=month&supplier={self.sup}")
            self.get(f"/api/estock/period?from=2026-10-01&to=2026-11-20&by=day")
            self.get(f"/api/estock/invoices?kind=purchase&{RANGE}&limit=500")
            self.get(f"/api/estock/customers/{self.cust}/items")
        self.assertEqual(self.db_hash(), before_db)
        self.assertEqual(self.our_reports(), before_rep)


STEP2 = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, cust] = process.argv.slice(1);
""" + LOGIN + r"""
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));
  const out = {};
  const range = async (id, f, t) => {
    const d = await p.$$('#' + id + ' input[type=date]');
    await d[0].fill(f); await d[0].dispatchEvent('change');
    await d[1].fill(t); await p.click('#' + id + ' [data-rgo]');
  };
  await p.goto(base + '/#/customers/' + cust);
  await p.waitForSelector('#esItems tbody tr');
  out.items = await p.textContent('#esItems');
  await p.goto(base + '/#/reports');
  await p.waitForSelector('#rRange input[type=date]');
  await range('rRange', '2026-10-01', '2026-11-20');
  await p.waitForFunction(() => document.querySelector('#esRep') && document.querySelector('#rSalesBox h2').textContent.includes('2026-10-01'));
  out.rep = await p.textContent('#esRep');
  out.repRows = await p.$$eval('#esRep tbody tr', (t) => t.map((x) => x.innerText.includes('من إي ستوك')));
  await p.goto(base + '/#/purchases');
  await p.waitForSelector('#puRange input[type=date]');
  await range('puRange', '2026-09-01', '2026-11-20');
  await p.waitForSelector('#esPur tr[data-go]');
  out.pur = await p.textContent('#esPur');
  out.purRows = await p.$$eval('#esPur tr[data-go]', (t) => t.map((x) => x.innerText.includes('من إي ستوك')));
  await p.fill('#puQ', 'xyz');
  out.goneOnSearch = (await p.$('#esPur')) === null;
  console.log(JSON.stringify({ ...out, errs }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestStep2Screens(Step2Base):
    def test_report_purchases_and_customer_show_the_history_tagged_and_write_nothing(self):
        before = self.db_tables()
        out = run_node(self, STEP2, "owner", OWNER_PW, self.cust)
        self.assertEqual(out["errs"], [])
        self.assertIn("من إي ستوك", out["items"])
        es = self.get("/api/estock/period?from=2026-10-01&to=2026-11-20&by=month")["total"]
        # the program sold nothing then: the combined line is E-Stock's sales before returns, exactly
        self.assertIn(f"البرنامج + إي ستوك: {es['sales']} ج", out["rep"])
        self.assertTrue(out["repRows"] and all(out["repRows"]))
        self.assertTrue(out["purRows"] and all(out["purRows"]))
        self.assertIn("المعتمد في البرنامج + إي ستوك", out["pur"])
        self.assertTrue(out["goneOnSearch"])
        after = self.db_tables()
        self.assertEqual({t for t in after if after[t] != before.get(t)} - {"audit_log"}, set())


class TestCodexFixes437(EverywhereBase):
    def test_excel_sheet_keeps_the_unreadable_count(self):
        from pathlib import Path
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        i = js.index("const estockRepSheet")
        body = js[i:js.index("};", js.index("return [{", i))]
        self.assertIn("_unreadable", body)
        self.assertIn("مش مقروء", body)
