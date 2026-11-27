"""Purchase-plan quotes and latency against synthetic E-Stock archives."""
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from time import perf_counter

from app.catalog import card
from app.catalog import service as cat
from app.dataimport.estock_archive import NAME
from app.dataimport.estock_synthetic import FULL
from app.procurement import demand
from tests.test_api import APICase, H
from tests.test_u4_purchase_plan import TODAY, PlanBase


def archive(path, org_id):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE map_items (estock_id TEXT PRIMARY KEY, program_id TEXT NOT NULL);
        CREATE TABLE map_suppliers (estock_id TEXT PRIMARY KEY, program_id TEXT NOT NULL);
        CREATE TABLE e_Vendor (vendor_id TEXT, vendor_name_ar TEXT);
        CREATE TABLE e_Products (product_id TEXT, product_code TEXT, sell_price TEXT);
        CREATE TABLE e_Purchase_header (purchase_id TEXT, vendor_id TEXT, bill_date TEXT, insert_date TEXT);
        CREATE TABLE e_Purchase_details (
            details_id TEXT, purchase_id TEXT, product_id TEXT, amount TEXT, bouns TEXT, sell_price TEXT, buy_price TEXT
        );
        CREATE INDEX purchase_at ON e_Purchase_header(insert_date);
        CREATE INDEX purchase_id ON e_Purchase_header(purchase_id);
        CREATE INDEX purchase_lines ON e_Purchase_details(purchase_id);
        CREATE INDEX purchase_product ON e_Purchase_details(product_id);
        CREATE TABLE e_Sales_header (sales_id TEXT, insert_date TEXT);
        CREATE TABLE e_Sales_details (sales_id TEXT, product_id TEXT, amount TEXT, back_amount TEXT);
        CREATE INDEX sale_at ON e_Sales_header(insert_date);
        CREATE INDEX sale_lines ON e_Sales_details(sales_id);
    """)
    con.execute("INSERT INTO meta VALUES ('org_id', ?)", (org_id,))
    return con


class TestArchiveQuotes(PlanBase):
    def setUp(self):
        super().setUp()
        self.it = self.item("Synthetic Order Item", public_price=11000, pack_size=4)
        card.set_shortage(self.db, self.ctx, self.it, True)
        self.ibn = cat.create_supplier(self.db, self.ctx, name="ابن سينا")
        self.path = self.tmp / NAME
        with closing(archive(self.path, self.ctx.org_id)) as con, con:
            con.execute("INSERT INTO map_items VALUES ('901', ?)", (self.it,))
            con.execute("INSERT INTO map_suppliers VALUES ('84', ?)", (self.ibn,))
            con.execute("INSERT INTO e_Vendor VALUES ('84', 'ابن سينا'), ('85', 'مورد تجريبي قديم')")
            con.execute("INSERT INTO e_Products VALUES ('901', 'E-901', '100.00')")

    def old_buy(self, inv, *, days_ago=4, vendor="84", price="100.00", net="85.00", qty="10", bonus="2"):
        at = (TODAY - timedelta(days=days_ago)).isoformat() + " 18:00:00"
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("INSERT INTO e_Purchase_header VALUES (?, ?, ?, ?)", (inv, vendor, at, at))
            con.execute("INSERT INTO e_Purchase_details VALUES (?, ?, '901', ?, ?, ?, ?)",
                        (inv, inv, qty, bonus, price, net))

    def test_ibn_sina_archive_only_has_real_discount(self):
        self.old_buy("1")
        before = self.path.read_bytes()
        out = self.plan()
        row = out["rows"][0]
        self.assertIsNotNone(row["best"])
        self.assertEqual((row["best"]["supplier_id"], row["best"]["supplier"]), (self.ibn, "ابن سينا"))
        self.assertEqual(row["best"]["discount_bp"], 2917)
        self.assertEqual((row["best"]["bonus"], row["best"]["unit_net_price"]), (2, 8500))
        self.assertEqual(out["ask_about"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invoice_day_latest_across_sources_and_live_wins_a_tie(self):
        self.old_buy("1", days_ago=7, net="70")
        self.old_buy("2", days_ago=4, net="85")
        self.buy(self.it, self.ibn, days_ago=4, public=10000, net=9000)
        best = self.rows()[self.it]["best"]
        self.assertEqual(best["discount_bp"], 1000)
        self.assertEqual(best["prev_bp"], 2917)
        self.old_buy("3", days_ago=2, net="75")
        best = self.rows()[self.it]["best"]
        self.assertEqual(best["discount_bp"], 3750)
        self.assertEqual(best["prev_bp"], 1000)

    def test_unmapped_supplier_listed_and_cannot_be_recommended(self):
        self.old_buy("1", vendor="85", net="60")
        row = self.rows()[self.it]
        self.assertIsNone(row["best"])
        self.assertEqual([(s["supplier_id"], s["supplier"], s["estock_supplier_id"]) for s in row["suppliers"]],
                         [(None, "مورد تجريبي قديم", "85")])

    def test_archive_prices_are_hidden_without_permission(self):
        self.old_buy("1")
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"costs.read"})
        row = self.plan(ctx=staff)["rows"][0]
        self.assertIsNone(row["best"])
        self.assertEqual(row["suppliers"][0]["supplier"], "ابن سينا")
        for key in ("discount_bp", "prev_bp", "unit_net_price", "bonus"):
            self.assertIsNone(row["suppliers"][0][key])
            self.assertNotIn(key, row["last"])

    def test_archive_is_limited_to_its_org_branch_and_purchase_window(self):
        self.old_buy("1", days_ago=366)
        self.old_buy("2", days_ago=-1)
        self.assertEqual(self.rows()[self.it]["suppliers"], [])
        self.old_buy("3", days_ago=10, price=None, qty="2.50", bonus="0.50", net="90.0000")
        self.assertEqual(self.rows()[self.it]["best"]["discount_bp"], 2500)
        self.assertEqual(self.plan(ctx=replace(self.ctx, branch_id="other"))["rows"][0]["suppliers"], [])
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("UPDATE meta SET value='other' WHERE key='org_id'")
        self.assertEqual(self.rows()[self.it]["suppliers"], [])


class TestPlanAtArchiveScale(APICase):
    def test_first_get_with_30k_items_and_full_archive(self):
        self.http_login()
        supplier = cat.create_supplier(self.db, self.ctx, name="ابن سينا")
        with self.db.transaction():
            self.db.execute("""
                WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<?)
                INSERT INTO items (id, org_id, name_en, name_norm, dedupe_key, public_price, pack_size)
                SELECT 'scale-'||x, ?, 'Synthetic '||x, 'synthetic '||x, 'scale-'||x, 10000, 4 FROM n
            """, (FULL["items"], self.ctx.org_id))
            self.db.execute("""
                INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty)
                SELECT org_id, ?, id, '', 8 FROM items WHERE org_id=? AND id NOT LIKE 'scale-%'
                    OR org_id=? AND CAST(substr(id,7) AS INTEGER)>420
            """, (self.ctx.branch_id, self.ctx.org_id, self.ctx.org_id))

        today = date.today()
        month = today.replace(day=1)
        months = []
        for _ in range(12):
            month = (month - timedelta(days=1)).replace(day=1)
            months.append(month.isoformat() + " 14:00:00")
        with closing(archive(self.tmp / NAME, self.ctx.org_id)) as con, con:
            con.executemany("INSERT INTO map_items VALUES (?,?)",
                            ((str(i), f"scale-{i}") for i in range(1, FULL["items"] + 1)))
            con.executemany("INSERT INTO e_Products VALUES (?,?,'100.00')",
                            ((str(i), f"E-{i}") for i in range(1, FULL["items"] + 1)))
            con.execute("INSERT INTO map_suppliers VALUES ('84',?)", (supplier,))
            con.executemany("INSERT INTO e_Sales_header VALUES (?,?)",
                            ((i, months[(i - 1) // 420 % 12]) for i in range(1, FULL["sales"] + 1)))
            con.executemany("INSERT INTO e_Sales_details VALUES (?,?,?,?)",
                            ((1 + i % FULL["sales"], 1 + i % 420 if i < 5040 else 421 + i % 29580,
                              "2.50", "0.50") for i in range(FULL["sale_lines"])))
            con.executemany("INSERT INTO e_Purchase_header VALUES (?,'84',?,?)",
                            ((i, months[i % 12], months[i % 12]) for i in range(1, FULL["purchases"] + 1)))
            con.executemany("INSERT INTO e_Purchase_details VALUES (?,?,?,'10','2','100.00','85.00')",
                            ((i, 1 + i % FULL["purchases"], 1 + i % 420 if i < 8400 else 421 + i % 29580)
                             for i in range(FULL["purchase_lines"])))
        demand._cache.clear()
        started = perf_counter()
        response = self.c.get("/api/procurement/plan", headers=H)
        elapsed = perf_counter() - started
        print(f"GET /api/procurement/plan, first request at archive scale: {elapsed:.3f}s")
        self.assertEqual(response.status_code, 200, response.text[:200])
        rows = response.json()["rows"]
        self.assertEqual(len(rows), 420)
        self.assertTrue(all((row["best"] or {}).get("discount_bp") == 2917 for row in rows))
        self.assertLess(elapsed, 5.0, "first request with a full archive exceeded the CI timing budget")
