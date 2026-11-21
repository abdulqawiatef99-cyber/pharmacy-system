"""Update 1 (launch day 21/11): search finds the code typed in full first («3796» found nothing), queries of
several words in any order, Arabic spelling variants (بكتول / بيكتول, ى / ي, ة / ه) for items and customers
(جالهوم / جلهوم); on the sale screen items with stock first and the ones without left out unless asked."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import search, service as cat
from app.inventory import service as inv
from app.sales import customers
from tests.helpers import DBTestCase
from tests.test_api import H, APICase


class ItemSearch(DBTestCase):
    def setUp(self):
        super().setUp()
        mk = lambda **f: cat.create_item(self.db, self.ctx, {"pack_size": 1, "public_price": 1000, **f})  # noqa: E731
        self.code = mk(name_en="Testcode Tab", code="3796")
        self.ecode = mk(name_en="Testmoved Tab", code="E5120")
        self.bec = mk(name_en="Bectol Testcream", name_ar="بكتول كريم")
        self.hosp = mk(name_en="Testward", name_ar="مستشفى الحكمة شراب")
        self.decoy = mk(name_en="Test 3796 decoy")

    def ids(self, q):
        return [i["id"] for i in search.search(self.db, self.ctx, q, 8)["items"]]

    def test_exact_code_first(self):
        self.assertEqual(self.ids("3796")[0], self.code)
        self.assertEqual(self.ids("5120")[0], self.ecode)             # moved without a code: E<id>
        self.assertEqual(self.ids("e5120")[0], self.ecode)

    def test_words_in_any_order_and_across_languages(self):
        self.assertIn(self.bec, self.ids("كريم بكتول"))
        self.assertIn(self.bec, self.ids("bectol كريم"))

    def test_arabic_spelling_variants(self):
        self.assertIn(self.bec, self.ids("بيكتول"))
        self.assertIn(self.bec, self.ids("بيكتول كريم"))
        self.assertIn(self.hosp, self.ids("مستشفي الحكمه"))
        self.assertNotIn(self.bec, self.ids("مستشفي"))


class CustomerSearch(DBTestCase):
    def test_spelling_variants(self):
        from app.core.permissions import Context  # noqa: F401
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name) VALUES ('c1', ?, 'محمد جالهوم')", (self.ctx.org_id,))
            self.db.execute("INSERT INTO customers (id, org_id, name) VALUES ('c2', ?, 'مستشفى الحكمة')", (self.ctx.org_id,))
        names = lambda q: [c["id"] for c in customers.listing(self.db, self.ctx, q)]  # noqa: E731
        self.assertEqual(names("جلهوم"), ["c1"])
        self.assertEqual(names("جالهوم"), ["c1"])
        self.assertEqual(names("مستشفي الحكمه"), ["c2"])
        self.assertEqual(names("ا"), sorted(names("ا")))                # a vowel alone: no crash, plain LIKE


class SaleScreenStock(APICase):
    def test_stock_first_and_only(self):
        a = cat.create_item(self.db, self.ctx, {"name_en": "Testpan Zero", "public_price": 1000})
        b = cat.create_item(self.db, self.ctx, {"name_en": "Testpan Stocked", "public_price": 1000})
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, b, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=b, qty_delta=4, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("5"))
        self.http_login()
        get = lambda s: self.c.get(f"/api/items?q=testpan&limit=8&meta=1{s}", headers=H).json()  # noqa: E731
        self.assertEqual([i["id"] for i in get("&stock=first")["items"]], [b, a])
        r = get("&stock=only")
        self.assertEqual(([i["id"] for i in r["items"]], r["hidden_no_stock"]), ([b], 1))
        self.assertEqual({i["id"] for i in get("")["items"]}, {a, b})          # other screens: unchanged


from tests.helpers import OWNER_PW, needs_browser  # noqa: E402
from tests.pos_browser import LOGIN, run_node  # noqa: E402

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  const out = {};
  await p.locator('#posQ').fill('testpan');
  await p.waitForSelector('#posRes .res');
  await p.waitForTimeout(400);
  out.first = await p.locator('#posRes .res').count();
  out.note = await p.locator('#posShowZero').count();
  await p.locator('#posShowZero').click();
  await p.waitForFunction(() => document.querySelectorAll('#posRes .res').length === 2);
  out.all = await p.locator('#posRes .res').count();
  out.kept = await p.locator('#posAll').isChecked();
  await p.reload();
  await p.waitForSelector('#posQ');
  out.afterReload = await p.locator('#posQ').inputValue();
  out.stillAll = await p.locator('#posAll').isChecked();
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class SaleScreenToggleInBrowser(APICase):
    def test_zero_stock_hidden_then_shown(self):
        from app.sales.cash import CashDrawer
        cat.create_item(self.db, self.ctx, {"name_en": "Testpan Zero", "public_price": 1000})
        b = cat.create_item(self.db, self.ctx, {"name_en": "Testpan Stocked", "public_price": 1000})
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, b, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=b, qty_delta=4, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("5"))
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertEqual((out["first"], out["note"], out["all"]), (1, 1, 2))
        self.assertTrue(out["kept"] and out["stillAll"])
        self.assertEqual(out["afterReload"], "")


class CodexReview400(DBTestCase):
    """Codex on #400 @a3eb012 (each failed before the fix)."""

    def test_p1_a_strength_stays_required_in_the_spelling_search(self):
        b250 = cat.create_item(self.db, self.ctx, {"name_ar": "بكتول 250 كريم", "name_en": "Testbec 250", "pack_size": 1,
                                                   "public_price": 1000})
        r = search.search(self.db, self.ctx, "بيكتول 500", 8)
        self.assertNotIn(b250, [i["id"] for i in r["items"]])
        r = search.search(self.db, self.ctx, "بيكتول 250", 8)
        self.assertEqual([i["id"] for i in r["items"]], [b250])
        self.assertEqual(r["hint"], {"kind": "spelling"})        # a hint: Enter never auto-adds a spelling guess


class CodexStockBeyondTheFirstMatches(APICase):
    def test_p2_a_stocked_item_after_many_zero_stock_names_is_found(self):
        for n in range(90):
            cat.create_item(self.db, self.ctx, {"name_en": f"Testzz {n:02d}", "public_price": 1000})
        far = cat.create_item(self.db, self.ctx, {"name_en": "Testzz with a much longer name in stock", "public_price": 1000})
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, far, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=far, qty_delta=4, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("5"))
        self.http_login()
        r = self.c.get("/api/items?q=testzz&limit=8&meta=1&stock=only", headers=H).json()
        self.assertEqual([i["id"] for i in r["items"]], [far])
