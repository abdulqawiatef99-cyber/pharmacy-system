"""QA-29 PR1: heavy exports and reports never stop the cashier, and long lists never cut a total.

Independent tester, synthetic data, 30,000 items (develop 95416e7):
1. «تصدير Excel» on the items screen stopped the whole program for ~170 s. Measured here: 89.9 s at 30k items.
   Causes: one barcode question per item on a table with no item_id index (30k scans of 30k rows), and the
   export ran on the server's event loop, so every other request (the sale screen too) waited for it.
2. The purchase list is capped at 5000 rows and the screen added up only what it got: a long period showed a
   short total with no word about it.
3. The stock health panel (/api/procurement/health) sent every slow mover at once (3.9 MB at 30k items) and the
   item data check could send one row per problem of every item (20 MB worst case).
"""
import asyncio
import re
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.dataimport import service as dimp
from app.inventory import service as inventory
from app.procurement import purchase_plan, smart_order
from app.procurement import service as proc
from app.purchasing import service as purch_svc
from app.reports import item_health as ih
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

APP_JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


@contextmanager
def spy(module, name, seen: dict):
    real = getattr(module, name)

    def wrapper(*a, **kw):
        try:
            asyncio.get_running_loop()
            seen[name] = "loop"
        except RuntimeError:
            seen[name] = "worker"
        return real(*a, **kw)

    setattr(module, name, wrapper)
    try:
        yield
    finally:
        setattr(module, name, real)


def selects(db, fn):
    """Run fn and count the SELECT statements it sent on this thread's connection."""
    n = [0]

    def trace(sql):
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            n[0] += 1

    db.conn.set_trace_callback(trace)
    try:
        out = fn()
    finally:
        db.conn.set_trace_callback(None)
    return out, n[0]


class TestItemsExport(DBTestCase):
    def _bulk(self, n):
        o = self.ctx.org_id
        with self.db.transaction():
            self.db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<?)"
                            " INSERT INTO items (id, org_id, name_en, name_norm, dedupe_key, public_price, pack_size)"
                            " SELECT 'q-'||x, ?, 'Fixture '||x, 'fixture '||x, 'q-'||x, 1000+x, 2 FROM n", (n, o))
            self.db.execute("INSERT INTO item_barcodes (org_id, item_id, barcode) SELECT org_id, id, 'B'||id FROM items"
                            " WHERE org_id=?", (o,))
            self.db.execute("INSERT INTO item_barcodes (org_id, item_id, barcode) VALUES (?, 'q-1', 'SECOND')", (o,))

    def test_export_asks_a_fixed_number_of_questions_whatever_the_catalog_size(self):
        self._bulk(300)
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id="q-1", qty_delta=7, batch_id=None,
                                    movement_type="opening", unit_cost=Decimal("1"))
        data, n = selects(self.db, lambda: dimp.export_catalog(self.db, self.ctx))
        self.assertLessEqual(n, 6, "one question per item made 30k items take minutes")
        from io import BytesIO
        from openpyxl import load_workbook
        ws = load_workbook(BytesIO(data)).active
        rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True)}
        head = [c.value for c in ws[1]]
        self.assertEqual(len(rows), 300)
        one = dict(zip(head, rows["q-1"]))
        self.assertEqual(sorted(one["barcodes"].split(",")), ["Bq-1", "SECOND"])
        self.assertEqual(one["stock_base_units"], 7)
        self.assertEqual(dict(zip(head, rows["q-2"]))["stock_base_units"], 0)

    def test_hidden_items_and_stock_follow_the_permissions(self):
        self._bulk(3)
        with self.db.transaction():
            self.db.execute("UPDATE items SET hidden=1 WHERE id='q-3'")
        from io import BytesIO
        from openpyxl import load_workbook
        from app.core.permissions import Context
        staff = Context(org_id=self.ctx.org_id, branch_id=self.ctx.branch_id, user_id=self.ctx.user_id,
                        permissions=frozenset({"items.read"}))
        ws = load_workbook(BytesIO(dimp.export_catalog(self.db, staff))).active
        head = [c.value for c in ws[1]]
        self.assertNotIn("stock_base_units", head)
        self.assertNotIn("q-3", {r[0] for r in ws.iter_rows(min_row=2, values_only=True)})


class TestHeavyRoutesOffTheLoop(APICase):
    def test_items_export_runs_in_a_worker_thread(self):
        self.http_login()
        seen = {}
        with spy(dimp, "export_catalog", seen):
            r = self.c.get("/api/export/items.xlsx", headers=H)
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertEqual(seen.get("export_catalog"), "worker", "the export stopped every other request")

    def test_order_and_suggestion_workbooks_run_in_a_worker_thread(self):
        self.http_login()
        seen = {}
        with spy(proc, "suggestions_xlsx", seen), spy(purchase_plan, "order_xlsx", seen):
            r1 = self.c.get("/api/procurement/suggestions.xlsx", headers=H)
            r2 = self.c.post("/api/procurement/plan.xlsx", headers=H, json={"groups": []})
        self.assertEqual(r1.status_code, 200, r1.text[:200])
        self.assertEqual(r2.status_code, 422, r2.text[:200])        # an empty order, refused off the loop
        self.assertEqual(seen, {"suggestions_xlsx": "worker", "order_xlsx": "worker"})
        seen = {}
        with spy(smart_order, "order_xlsx", seen):
            r3 = self.c.post("/api/procurement/order.xlsx", headers=H, json={"groups": []})
        self.assertEqual(r3.status_code, 422, r3.text[:200])
        self.assertEqual(seen, {"order_xlsx": "worker"})

    def test_items_screen_keeps_the_export_out_of_the_daily_toolbar(self):
        body = APP_JS[APP_JS.index("async function viewItems()"):]
        body = body[:body.index("\n}\n")]
        toolbar = body[:body.index('<div class="row"><label class="f grow">')]
        menu = re.search(r"<details[^>]*>(.*?)</details>", toolbar, re.S)
        self.assertIsNotNone(menu, "the export sits in a menu, not as a toolbar link")
        self.assertTrue("/api/export/items.xlsx" in menu.group(1), "/api/export/items.xlsx")
        self.assertTrue("بياخد وقت" in menu.group(1), "بياخد وقت")
        self.assertNotIn("/api/export/items.xlsx", toolbar.replace(menu.group(0), ""))


class TestPurchaseListTotals(APICase):
    def _inv(self, n, status, total, day="2026-03-10"):
        with self.db.transaction():
            for k in range(n):
                self.db.execute(
                    "INSERT INTO purchase_invoices (id, org_id, branch_id, status, source_type, invoice_no, invoice_date,"
                    " computed_total, declared_total) VALUES (?,?,?,?, 'manual', ?, ?, ?, ?)",
                    (f"pi-{status}-{day}-{k}", self.ctx.org_id, self.ctx.branch_id, status, f"N-{status}-{day}-{k}", day,
                     total, total + 1))

    def test_totals_cover_every_matching_invoice_beyond_the_list_cap(self):
        self._inv(4, "approved", 1000)
        self._inv(2, "draft", 500)
        self._inv(1, "approved", 9999, day="2025-01-01")            # outside the period
        old = purch_svc.LIST_CAP
        purch_svc.LIST_CAP = 3
        try:
            self.http_login()
            r = self.c.get("/api/purchases?from=2026-03-01&to=2026-03-31&totals=1", headers=H)
        finally:
            purch_svc.LIST_CAP = old
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(len(d["rows"]), 3)
        self.assertTrue(d["capped"])
        self.assertEqual(d["totals"], {"count": 6, "approved_count": 4, "approved_total": 4000})

    def test_supplier_and_status_filters_apply_to_the_totals(self):
        self._inv(2, "approved", 1000)
        sup = cat.create_supplier(self.db, self.ctx, name="Synthetic Supplier QA29")
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoices SET supplier_id=? WHERE id='pi-approved-2026-03-10-0'", (sup,))
        self.http_login()
        d = self.c.get(f"/api/purchases?from=2026-03-01&to=2026-03-31&supplier={sup}&totals=1", headers=H).json()
        self.assertEqual(d["totals"], {"count": 1, "approved_count": 1, "approved_total": 1000})
        self.assertFalse(d["capped"])

    def test_cancelled_null_computed_and_other_pharmacy(self):
        self._inv(2, "approved", 1000)
        self._inv(1, "cancelled", 7000)
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoices SET computed_total=NULL WHERE id='pi-approved-2026-03-10-1'")
        from app.core import accounts
        other = accounts.create_organization(self.db, name="Synthetic Other", owner_username="other", owner_full_name="Other",
                                             owner_password="Other-Pass-2026")
        with self.db.transaction():
            self.db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, status, source_type, invoice_date,"
                            " computed_total) VALUES ('x-org', ?, ?, 'approved', 'manual', '2026-03-10', 55555)",
                            (other["org_id"], other["branch_id"]))
        self.http_login()
        d = self.c.get("/api/purchases?from=2026-03-01&to=2026-03-31&totals=1", headers=H).json()
        # 1000 + the declared 1001 of the one with no computed total; the cancelled one counts as a row only
        self.assertEqual(d["totals"], {"count": 3, "approved_count": 2, "approved_total": 2001})

    def test_plain_list_keeps_its_shape(self):
        self._inv(1, "draft", 500)
        self.http_login()
        self.assertIsInstance(self.c.get("/api/purchases?status=draft", headers=H).json(), list)

    def test_screen_shows_the_server_totals_and_says_when_the_list_is_cut(self):
        body = APP_JS[APP_JS.index("const loadList = async (f, t) =>"):][:1500]
        self.assertTrue("totals" in body, "totals")
        draw = APP_JS[APP_JS.index("const drawList = () => {"):][:4000]
        self.assertTrue("capped" in draw, "capped")
        self.assertFalse("rows.length >= 5000" in draw, "rows.length >= 5000")


class TestHealthListsCapped(APICase):
    def _stock(self, n):
        with self.db.transaction():
            self.db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<?)"
                            " INSERT INTO items (id, org_id, name_en, name_norm, dedupe_key, public_price, pack_size)"
                            " SELECT 'h-'||x, ?, 'Fixture '||x, 'fixture '||x, 'h-'||x, 0, 3 FROM n", (n, self.ctx.org_id))
            self.db.execute("INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty)"
                            " SELECT org_id, ?, id, '', 5 FROM items WHERE id LIKE 'h-%'", (self.ctx.branch_id,))

    def test_stock_health_sends_the_first_200_and_the_full_counts(self):
        self._stock(230)
        self.http_login()
        d = self.c.get("/api/procurement/health", headers=H).json()
        self.assertEqual(len(d["slow_movers"]), 200)
        self.assertEqual(d["counts"]["slow_movers"], 230)
        more = self.c.get("/api/procurement/health?limit=400", headers=H).json()
        self.assertEqual(len(more["slow_movers"]), 230)

    def test_item_health_sends_the_first_200_and_filters_by_kind(self):
        self._stock(230)                                             # each has no public price
        self.http_login()
        d = self.c.get("/api/reports/item-health", headers=H).json()
        self.assertEqual(len(d["rows"]), 200)
        self.assertEqual(d["counts"]["no_public_price"], 230)
        self.assertEqual(d["total"], sum(d["counts"].values()))
        k = self.c.get("/api/reports/item-health?key=no_public_price&limit=500", headers=H).json()
        self.assertEqual({r["key"] for r in k["rows"]}, {"no_public_price"})
        self.assertEqual(len(k["rows"]), 230)

    def test_screens_offer_more_instead_of_everything(self):
        self.assertTrue("/api/procurement/health?limit=" in APP_JS, "/api/procurement/health?limit=")
        self.assertTrue("/api/reports/item-health?" in APP_JS, "/api/reports/item-health?")
