"""Update 1 (owner 21/11 20:10): «د محمد الشربيني» (E-Stock code 335) was not found. The customer search now matches
the E-Stock code exactly (ranked first), drops titles (د/ د. دكتور), normalises Arabic and takes the words in any
order, in POS and on the customers screen; stopped customers show under «عملاء موقوفين» with «رجّعه نشط».
Wording: a zero-stock badge says «نفد» («ناقص» = not in the market), the shortage list says «في النواقص»."""
import dataclasses
from pathlib import Path

from app.core.errors import Forbidden
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

    def test_stopped_customers_and_back(self):
        self.assertNotIn("off", self.ids("الشربيني"))
        self.assertEqual(self.ids("الشربيني", "inactive"), ["off"])
        cashier = dataclasses.replace(self.ctx, role_code="assistant",
                                      permissions=frozenset(self.ctx.permissions - {"customers.manage"}))
        with self.assertRaises(Forbidden):
            cu.set_active(self.db, cashier, "off", True)
        cu.set_active(self.db, self.ctx, "off", True)
        self.assertIn("off", self.ids("هالة"))
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='customer.active' AND entity_id='off'"))


class PosSearchApi(APICase):
    def test_pos_box_finds_by_code_and_title(self):
        add(self.db, self.ctx, "c335", "د/ محمد الشربينى", "كود إي ستوك: 335")
        self.http_login()
        for q in ("335", "د محمد الشربيني"):
            r = self.c.get(f"/api/customers?q={q}", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()[0]["id"], "c335", q)
        r = self.c.post("/api/customers/c335/active", json={"active": False}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/customers?q=335", headers=H).json(), [])


class Screens(DBTestCase):
    def test_wording_and_placeholders(self):
        self.assertEqual(JS.count('placeholder="الاسم أو الموبايل أو الكود"'), 2)
        self.assertIn('data-f="inactive"', JS)
        self.assertIn("رجّعه نشط", JS)
        self.assertNotIn('<span class="badge b-err">✕ ناقص</span>', JS)
        self.assertNotIn('<span class="badge b-err">ناقص</span>', JS)
        self.assertIn('<span class="badge b-warn">⚠ في النواقص</span>', JS)
