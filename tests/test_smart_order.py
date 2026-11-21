"""U2-36 smart order screen: movement classes, the default view hides the noise, last / best supplier with
the bonus counted, flags for the owner, the order exported per supplier."""
import io
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import card
from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.db import new_id
from app.inventory import service as inv
from app.procurement import smart_order as so
from tests.helpers import OWNER_PW, DBTestCase, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase
from tests.test_owner_plan_priority_1_2 import PlanCase

TODAY = date.today()


class SmartCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.fast = cat.create_item(self.db, self.ctx, {"name_en": "Fast Tab 20", "public_price": 5000})
        self.medium = cat.create_item(self.db, self.ctx, {"name_en": "Medium Syrup", "public_price": 4000})
        self.slow = cat.create_item(self.db, self.ctx, {"name_en": "Slow Cream", "public_price": 9000})
        self.asked = cat.create_item(self.db, self.ctx, {"name_en": "Asked Drops", "public_price": 2000})
        self.dear = cat.create_item(self.db, self.ctx, {"name_en": "Dear Pen", "public_price": 120000})
        self.s1 = cat.create_supplier(self.db, self.ctx, name="مورد أ", base_discount_local_bp=2000)
        self.s2 = cat.create_supplier(self.db, self.ctx, name="مورد ب")
        self._sell(self.fast, 15)          # 15 invoices in 90 days: fast
        self._sell(self.medium, 5)         # medium
        self._sell(self.slow, 1)           # the old screen offered one box for this
        self._sell(self.dear, 13)
        card.set_shortage(self.db, self.ctx, self.asked, True)

    def _sell(self, item, n):
        with self.db.transaction():
            for _ in range(n):
                inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=-1, movement_type="sale",
                                  ref_type="sale", ref_id=new_id(), allow_negative=True)

    def _buy(self, item, supplier, *, public, net, qty, bonus=0, shown=None, days_ago=10):
        pid = new_id()
        at = (TODAY - timedelta(days=days_ago)).isoformat() + "T10:00:00.000Z"
        with self.db.transaction():
            self.db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, supplier_id, invoice_no, invoice_date,"
                            " status, source_type, approved_at) VALUES (?,?,?,?,?,?,'approved','manual',?)",
                            (pid, self.ctx.org_id, self.ctx.branch_id, supplier, pid, at[:10], at))
            self.db.execute("INSERT INTO purchase_invoice_lines (id, invoice_id, org_id, line_no, raw_name, item_id, qty,"
                            " bonus_qty, public_price, discount_bp, unit_net_price) VALUES (?,?,?,1,'x',?,?,?,?,?,?)",
                            (new_id(), pid, self.ctx.org_id, item, qty, bonus, public, shown, net))

    def rows(self, **kw):
        return {r["item_id"]: r for r in so.smart_order(self.db, self.ctx, today=TODAY, **kw)["rows"]}


class TestClasses(SmartCase):
    def test_movement_classes(self):
        self.assertEqual([so.movement_class(n) for n in (0, 1, 3, 4, 11, 12)],
                         ["dead", "slow", "slow", "medium", "medium", "fast"])
        r = self.rows(scope="all")
        self.assertEqual((r[self.fast]["movement"], r[self.medium]["movement"], r[self.slow]["movement"]),
                         ("fast", "medium", "slow"))

    def test_default_view_hides_slow_noise_keeps_asked(self):
        out = so.smart_order(self.db, self.ctx, today=TODAY)
        ids = {r["item_id"] for r in out["rows"]}
        self.assertIn(self.fast, ids)
        self.assertIn(self.medium, ids)
        self.assertNotIn(self.slow, ids)                    # 1 sale in 90 days: hidden
        self.assertIn(self.asked, ids)                      # a customer asked, shelf empty, never sold
        self.assertEqual(out["counts"]["hidden"], 1)
        a = {r["item_id"]: r for r in out["rows"]}[self.asked]
        self.assertEqual(a["suggested_packs"], 1)
        self.assertTrue(a["asked"])
        self.assertEqual(out["rows"][0]["item_id"], self.asked)     # empty shelf a customer asked for: first
        self.assertIn(self.slow, self.rows(scope="all"))            # one click away
        self.assertFalse(self.rows(scope="all")[self.slow]["shown"])

    def test_slow_item_shown_when_a_customer_asked(self):
        card.set_shortage(self.db, self.ctx, self.slow, True)
        self.assertIn(self.slow, self.rows())

    def test_bad_scope_refused(self):
        with self.assertRaises(ValidationFailed):
            so.smart_order(self.db, self.ctx, today=TODAY, scope="x")


class TestSuppliers(SmartCase):
    def test_real_discount_counts_bonus(self):
        self.assertEqual(so.real_discount_bp(10000, 8000, 10, 0, 2000), 2000)
        # 10 + 2 at 20% shown: 1 - 0.8*10/12 = 33.33%
        self.assertEqual(so.real_discount_bp(10000, 8000, 10, 2, 2000), 3333)
        self.assertEqual(so.real_discount_bp(None, None, 10, 2, 2000), 3333)   # no net price: shown + bonus
        self.assertIsNone(so.real_discount_bp(None, None, 10, 0, None))

    def test_last_and_best_supplier(self):
        self._buy(self.fast, self.s1, public=5000, net=4000, qty=10, shown=2000, days_ago=30)      # 20%
        self._buy(self.fast, self.s2, public=5000, net=4250, qty=10, bonus=2, shown=1500, days_ago=5)  # 29.17%
        r = self.rows()[self.fast]
        self.assertEqual((r["last"]["supplier_id"], r["last"]["discount_bp"]), (self.s2, 2917))
        self.assertEqual(r["best"]["supplier_id"], self.s2)
        self.assertNotIn("below_official", r["flags"])

    def test_only_latest_price_per_supplier(self):
        self._buy(self.fast, self.s1, public=5000, net=3000, qty=10, days_ago=200)   # old deal 40%
        self._buy(self.fast, self.s1, public=5000, net=4500, qty=10, days_ago=20)    # today's 10%
        self._buy(self.fast, self.s2, public=5000, net=4250, qty=10, days_ago=40)    # 15%
        r = self.rows()[self.fast]
        self.assertEqual((r["best"]["supplier_id"], r["best"]["discount_bp"]), (self.s2, 1500))

    def test_flags(self):
        self._buy(self.dear, self.s1, public=120000, net=108000, qty=2, days_ago=3)   # 10% < official 20%
        c = card.save_category(self.db, self.ctx, {"name": "ثلاجة", "mark": "fridge"})
        card.set_category(self.db, self.ctx, [self.medium], c["id"])
        r = self.rows()
        self.assertEqual(set(r[self.dear]["flags"]), {"expensive", "below_official"})
        self.assertEqual(r[self.medium]["flags"], ["fridge"])
        self.assertEqual(r[self.fast]["flags"], [])

    def test_top_sellers_and_near_expiry(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.fast, "N1", (TODAY + timedelta(days=40)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.fast, qty_delta=1, batch_id=b, movement_type="opening",
                              unit_cost=Decimal("30"))
        r = self.rows()
        self.assertEqual(r[self.fast]["top_rank"], 0)
        self.assertEqual(r[self.fast]["near_expiry"], (TODAY + timedelta(days=40)).isoformat())
        self.assertIsNone(r[self.medium]["near_expiry"])

    def test_other_tenant_sees_nothing(self):
        _, other = self.second_tenant()
        self.assertEqual(so.smart_order(self.db, other, today=TODAY)["rows"], [])


class TestOrderExport(SmartCase):
    def test_xlsx_one_sheet_per_supplier(self):
        import openpyxl
        data = so.order_xlsx(self.db, self.ctx, [
            {"supplier_id": self.s1, "lines": [{"item_id": self.fast, "packs": 3}]},
            {"supplier_id": None, "lines": [{"item_id": self.asked, "packs": "1"}]}])
        wb = openpyxl.load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames, ["مورد أ", "بدون مورد سابق"])
        self.assertEqual([c.value for c in wb["مورد أ"][3]][::3], ["Fast Tab 20", 3])

    def test_xlsx_refuses_bad_lines(self):
        for g in ([], [{"supplier_id": None, "lines": [{"item_id": self.fast, "packs": 0}]}],
                  [{"supplier_id": None, "lines": [{"item_id": self.fast, "packs": 1.5}]}]):
            with self.assertRaises(ValidationFailed):
                so.order_xlsx(self.db, self.ctx, g)
        _, other = self.second_tenant()
        from app.core.errors import NotFound
        with self.assertRaises(NotFound):
            so.order_xlsx(self.db, other, [{"supplier_id": None, "lines": [{"item_id": self.fast, "packs": 1}]}])


class TestApi(PlanCase):
    def test_screen_and_export_routes(self):
        out = self.call("GET", "/api/procurement/smart?scope=all&expensive_from=50")
        self.assertIn("rows", out)
        self.assertEqual(out["expensive_from"], 5000)
        self.call("GET", "/api/procurement/smart?expensive_from=1.234", 422)
        self.call("GET", "/api/procurement/smart?scope=bad", 422)
        r = self.call("POST", "/api/procurement/order.xlsx",
                      json={"groups": [{"supplier_id": None, "lines": [{"item_id": self.pan, "packs": 2}]}]})
        self.assertTrue(r.content.startswith(b"PK"))
        self.call("POST", "/api/procurement/order.xlsx", 422, json={"groups": []})


BROWSER = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, shot] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/procurement');
  await p.waitForSelector('#prBody .prAdd');
  const names = async () => p.$$eval('#prBody tr td:first-child', (t) => t.map((x) => x.childNodes[0].textContent.trim()));
  const out = { first: await names() };
  await p.click('th[data-sort=label]'); out.asc = await names();
  await p.click('th[data-sort=label]'); out.desc = await names();
  await p.click('[data-chip=asked]'); out.asked = await names();
  await p.click('[data-chip=asked]');
  await p.click('#prBody .prAdd');
  out.order = await p.textContent('#prOrder');
  if (shot) await p.screenshot({ path: shot, fullPage: true });
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScreen(APICase):
    def test_sort_filter_add(self):
        import os
        self.ctx = self.login("owner", OWNER_PW)
        a = cat.create_item(self.db, self.ctx, {"name_en": "Alpha Tab", "public_price": 1000})
        z = cat.create_item(self.db, self.ctx, {"name_en": "Zeta Tab", "public_price": 1000})
        with self.db.transaction():
            for it, n in ((a, 13), (z, 5)):
                for _ in range(n):
                    inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=-1, movement_type="sale",
                                      ref_type="sale", ref_id=new_id(), allow_negative=True)
        card.set_shortage(self.db, self.ctx, z, True)
        out = run_node(self, BROWSER, "owner", OWNER_PW, os.environ.get("PHARM_SHOT", ""))
        self.assertEqual(out["asc"], ["Alpha Tab", "Zeta Tab"])
        self.assertEqual(out["desc"], ["Zeta Tab", "Alpha Tab"])
        self.assertEqual(out["asked"], ["Zeta Tab"])
        self.assertIn("بدون مورد سابق", out["order"])


class TestReviewFixes(SmartCase):
    def test_below_min_stock_is_shown_even_if_slow(self):
        with self.db.transaction():
            self.db.execute("UPDATE items SET min_stock=2 WHERE id=?", (self.slow,))
        self.assertTrue(self.rows()[self.slow]["shown"])

    def test_stopped_supplier_is_not_the_best(self):
        from app.procurement import suppliers
        self._buy(self.fast, self.s1, public=5000, net=3000, qty=10, days_ago=5)      # 40%, then stopped
        self._buy(self.fast, self.s2, public=5000, net=4500, qty=10, days_ago=6)      # 10%
        suppliers.update(self.db, self.ctx, self.s1, {"is_active": False})
        r = self.rows()[self.fast]
        self.assertEqual(r["best"]["supplier_id"], self.s2)
        self.assertEqual(r["last"]["supplier_id"], self.s1)     # the last purchase is still history

    def test_imported_history_counts_inside_window_and_top(self):
        from app.procurement.service import Procurement
        it = cat.create_item(self.db, self.ctx, {"name_en": "Strip Item", "pack_size": 3, "public_price": 3000})
        end = TODAY - timedelta(days=1)
        Procurement(self.db, today=TODAY).record_consumption(self.ctx, it, (end - timedelta(days=29)).isoformat(),
                                                              end.isoformat(), 2)      # 2 strips in a month
        r = self.rows(scope="all")[it]
        self.assertEqual(r["movement"], "slow")                # not "dead"
        self.assertIsNotNone(r["top_rank"])


class TestCodexRound1(SmartCase):
    def test_discounts_hidden_without_costs_read(self):
        from dataclasses import replace
        self._buy(self.fast, self.s1, public=5000, net=4000, qty=10, bonus=2, days_ago=5)
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"costs.read"})
        r = {x["item_id"]: x for x in so.smart_order(self.db, staff, today=TODAY)["rows"]}[self.fast]
        self.assertIsNone(r["best"])
        self.assertEqual(set(r["last"]), {"supplier_id", "supplier", "date", "active"})
        self.assertNotIn("below_official", r["flags"])
        self.assertIsNone(r["official_bp"])

    def test_sheet_titles_never_loop(self):
        groups = []
        for name in ("A" * 28, "A" * 26 + "_2", "A" * 28 + "*B"):
            sid = cat.create_supplier(self.db, self.ctx, name=name)
            groups.append({"supplier_id": sid, "lines": [{"item_id": self.fast, "packs": 1}]})
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(so.order_xlsx(self.db, self.ctx, groups)))
        self.assertEqual(len(set(wb.sheetnames)), 3)


class TestCodexRound2(SmartCase):
    def test_unknown_latest_discount_hides_older_deals(self):
        self._buy(self.fast, self.s1, public=5000, net=3000, qty=10, days_ago=60)    # old 40%
        self._buy(self.fast, self.s1, public=None, net=4000, qty=10, days_ago=5)     # latest: discount unknown
        self._buy(self.fast, self.s2, public=5000, net=4000, qty=10, days_ago=10)    # 20%
        self.assertEqual(self.rows()[self.fast]["best"]["supplier_id"], self.s2)

    def test_last_purchase_says_if_the_supplier_is_stopped(self):
        from app.procurement import suppliers
        self._buy(self.fast, self.s1, public=5000, net=4000, qty=10, days_ago=5)
        suppliers.update(self.db, self.ctx, self.s1, {"is_active": False})
        r = self.rows()[self.fast]
        self.assertIsNone(r["best"])
        self.assertFalse(r["last"]["active"])
