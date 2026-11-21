"""Update 1 (owner 21/11 20:10): «د محمد الشربيني» (E-Stock code 335) was not found. The customer search now matches
the E-Stock code exactly (ranked first), drops titles (د/ د. دكتور), normalises Arabic and takes the words in any
order, in POS and on the customers screen; stopped customers show under «عملاء موقوفين» with «رجّعه نشط».
Wording: a zero-stock badge says «نفد» («ناقص» = not in the market), the shortage list says «في النواقص»."""
from pathlib import Path

from app.sales import customers as cu
from tests.helpers import DBTestCase
from tests.test_api import H, APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def add(db, ctx, cid, name, notes=None, active=1):
    with db.transaction():
        db.execute("INSERT INTO customers (id, org_id, name, notes, is_active) VALUES (?,?,?,?,?)",
                   (cid, ctx.org_id, name, notes, active))


class Search(DBTestCase):
    def setUp(self):
        super().setUp()
        add(self.db, self.ctx, "c335", "د/ محمد الشربينى", "كود إي ستوك: 335")
        add(self.db, self.ctx, "c3350", "أحمد سعيد", "كود إي ستوك: 3350؛ رقم: 012")
        add(self.db, self.ctx, "cx", "محمد 335 علي")                         # 335 inside a name: after the code
        add(self.db, self.ctx, "off", "د. هالة الشربيني", "كود إي ستوك: 900", active=0)

    def ids(self, q, only=None):
        return [c["id"] for c in cu.listing(self.db, self.ctx, q, only)]

    def test_the_owners_cases(self):
        for q in ("د محمد الشربيني", "محمد الشربيني", "الشربيني محمد", "دكتور محمد الشربينى", "د. محمد الشربينى",
                  "محمد  الشربينى"):
            self.assertIn("c335", self.ids(q), q)
        self.assertEqual(self.ids("335")[0], "c335")                          # the code first
        self.assertIn("cx", self.ids("335"))
        self.assertEqual(self.ids("3350")[0], "c3350")
        self.assertNotIn("c3350", self.ids("335"))                           # exact code, not a prefix
        self.assertEqual(self.ids("٣٣٥")[0], "c335")                          # Arabic-Indic digits (review)
        self.assertNotEqual(self.ids("3%")[:1], ["c3350"])                   # % and _ are not wildcards (review)
        self.assertNotIn("c335", self.ids("33_"))

    def test_nobody_is_hidden_and_a_deleted_one_is_tagged(self):
        # owner 21/11 20:34: «الشربيني» and his mobile found nothing: an E-Stock flag must never hide a customer
        self.assertIn("off", self.ids("الشربيني"))
        add(self.db, self.ctx, "del", "سامي تجربة", "كود إي ستوك: 901؛ محذوف في إي ستوك")
        row = next(c for c in cu.listing(self.db, self.ctx, "سامي") if c["id"] == "del")
        self.assertTrue(row["estock_deleted"])
        self.assertFalse(next(c for c in cu.listing(self.db, self.ctx, "335") if c["id"] == "c335")["estock_deleted"])


class PosSearchApi(APICase):
    def test_pos_box_finds_by_code_and_title(self):
        add(self.db, self.ctx, "c335", "د/ محمد الشربينى", "كود إي ستوك: 335")
        self.http_login()
        for q in ("335", "د محمد الشربيني"):
            r = self.c.get(f"/api/customers?q={q}", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()[0]["id"], "c335", q)
        add(self.db, self.ctx, "hid", "عميل مخفي تجربة", "كود إي ستوك: 77", active=0)
        r = self.c.get("/api/customers?q=77", headers=H).json()
        self.assertEqual(r[0]["id"], "hid")                                  # shown in POS too


class Screens(DBTestCase):
    def test_wording_and_placeholders(self):
        self.assertEqual(JS.count('placeholder="الاسم أو الموبايل أو الكود"'), 2)
        self.assertNotIn('data-f="inactive"', JS)                          # owner: no «موقوف/نشط» wording
        self.assertIn("محذوف في إي ستوك</span>", JS)
        self.assertNotIn('<span class="badge b-err">✕ ناقص</span>', JS)
        self.assertNotIn('<span class="badge b-err">ناقص</span>', JS)
        self.assertIn('<span class="badge b-warn">⚠ في النواقص</span>', JS)


class OneTimeRepair(DBTestCase):
    """Customers / suppliers the move created with is_active=0 come back, once, audited and counted."""

    def setUp(self):
        super().setUp()
        br = self.db.one("SELECT id FROM branches")["id"]
        with self.db.transaction():
            for run, kind in (("rc", "customers"), ("rs", "suppliers")):
                self.db.execute("INSERT INTO import_runs (id, org_id, branch_id, kind, source_system, filename, file_sha256,"
                                " mapping, options, status) VALUES (?,?,?,?, 'estock_db', 'x', ?, '{}', '{}', 'applied')",
                                (run, self.ctx.org_id, br, kind, run))
            self.db.execute("INSERT INTO customers (id, org_id, name, notes, is_active) VALUES ('m1', ?, 'د/ محمد الشربينى',"
                            " 'كود إي ستوك: 335', 0)", (self.ctx.org_id,))
            self.db.execute("INSERT INTO customers (id, org_id, name, is_active) VALUES ('own', ?, 'عميل البرنامج', 0)",
                            (self.ctx.org_id,))                              # not from the move: left alone
            self.db.execute("INSERT INTO suppliers (id, org_id, name, name_norm, is_active) VALUES ('s1', ?, 'مورد', 'mwrd', 0)",
                            (self.ctx.org_id,))
            for n, (run, tid) in enumerate((("rc", "m1"), ("rs", "s1"))):
                self.db.execute("INSERT INTO import_rows (id, run_id, row_no, raw, data, action, target_id)"
                                " VALUES (?,?,?,'{}','{}','create',?)", (f"ir{n}", run, n + 1, tid))

    def test_reactivated_once_audited_and_counted(self):
        from app.dataimport import estock_party_fix as fix
        self.assertEqual(fix.run(self.db), {self.ctx.org_id: 2})
        act = {r["id"]: r["is_active"] for r in self.db.all("SELECT id, is_active FROM customers")}
        self.assertEqual((act["m1"], act["own"]), (1, 0))
        self.assertEqual(self.db.one("SELECT is_active FROM suppliers WHERE id='s1'")[0], 1)
        self.assertEqual(fix.result(self.db, self.ctx.org_id), {"customers": 1, "suppliers": 1})
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='estock.parties_reactivated'"))
        with self.db.transaction():
            self.db.execute("UPDATE customers SET is_active=0 WHERE id='m1'")
        self.assertEqual(fix.run(self.db), {})                               # once only: a later choice is kept


class Speed(DBTestCase):
    def test_search_stays_fast_on_a_real_size_database(self):
        import time
        from app.catalog import search
        rows = [(f"i{n}", self.ctx.org_id, f"T{n}", f"testitem {n} tab", f"testitem {n} tab", f"صنف تجربة {n} أقراص",
                 f"صنف تجربه {n} اقراص", f"k{n}") for n in range(30000)]
        cust = [(f"c{n}", self.ctx.org_id, f"عميل تجربة {n}", f"كود إي ستوك: {n}") for n in range(5000)]
        with self.db.transaction():
            self.db.conn.executemany("INSERT INTO items (id, org_id, code, name_en, name_norm, name_ar, name_ar_norm, dedupe_key)"
                                     " VALUES (?,?,?,?,?,?,?,?)", rows)
            self.db.conn.executemany("INSERT INTO customers (id, org_id, name, notes) VALUES (?,?,?,?)", cust)
        from app.catalog import matching
        t = time.perf_counter(); matching.prewarm(self.db); cold = time.perf_counter() - t   # at start, in the background
        worst = 0.0
        for q in ("T29999", "صنف تيجربة 29999", "testitem 12345", "بيكتول"):
            t = time.perf_counter(); search.search(self.db, self.ctx, q, 8); worst = max(worst, time.perf_counter() - t)
        for q in ("4999", "عميل تجربه 4321", "تيجربة"):
            t = time.perf_counter(); cu.listing(self.db, self.ctx, q); worst = max(worst, time.perf_counter() - t)
        self.assertLess(worst, 1.0, f"slowest search {worst * 1000:.0f} ms")
        print(f"\nslowest search on 30,000 items / 5,000 customers: {worst * 1000:.0f} ms (index build at start {cold:.1f} s)")
