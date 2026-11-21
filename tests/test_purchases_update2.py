"""Update 2 purchases: U2-2 fast expiry entry, U2-22 near-expiry guard (a reason to approve)."""
from datetime import date, timedelta

from app.catalog import service as cat
from app.core.errors import Conflict
from app.db import new_id
from app.inventory import service as inv
from app.purchasing.model import parse_expiry
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase
from tests.test_u2_purchase_base import PurchaseBase


class TestFastExpiry(PurchaseBase):
    def test_compact_forms(self):
        self.assertEqual(parse_expiry("1128"), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("112028"), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("11/28"), ("2028-11-30", "month"))
        for bad in ("1328", "2027", "128", "11280"):
            self.assertEqual(parse_expiry(bad), (None, None), bad)


class TestNearExpiryGuard(PurchaseBase):
    def setUp(self):
        super().setUp()
        with self.db.transaction():       # sells one strip a day for 90 days -> 30 a month
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=-90, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)

    def codes(self, inv_id):
        d = self.p.get(self.ctx, inv_id)
        return d, {i["code"]: i for i in d["issues"] if i["level"] == "error" and not i["acknowledged"]}

    def test_more_than_will_sell_needs_a_reason(self):
        exp = (date.today() + timedelta(days=300)).isoformat()      # 210 days before "near": ~210 strips
        inv_id = self.draft(qty=300, expiry=exp)                        # 300 boxes of 1
        d, issues = self.codes(inv_id)
        i = issues["slow_before_expiry"]
        self.assertIn("هتبيع حوالي", i["message"])
        self.assertLess(i["details"]["will_sell"], 300)
        with self.assertRaises(Conflict):
            self.p.approve(self.ctx, inv_id, d["invoice"]["version"])
        d = self.p.acknowledge(self.ctx, inv_id, "slow_before_expiry", "عرض كبير من المورد", i["line_id"],
                               d["invoice"]["version"])
        self.assertNotIn("slow_before_expiry", self.codes(inv_id)[1])
        self.p.approve(self.ctx, inv_id, d["invoice"]["version"])

    def test_a_quantity_that_sells_in_time_passes(self):
        inv_id = self.draft(qty=20, expiry=(date.today() + timedelta(days=400)).isoformat())
        self.assertNotIn("slow_before_expiry", self.codes(inv_id)[1])

    def test_short_expiry_needs_a_reason(self):
        inv_id = self.draft(qty=5, expiry=(date.today() + timedelta(days=100)).isoformat())
        self.assertIn("short_expiry", self.codes(inv_id)[1])

    def test_new_item_never_sold_is_not_flagged(self):
        new = cat.create_item(self.db, self.ctx, {"name_en": "Brand New", "public_price": 1000})
        inv_id = self.draft(qty=300, expiry=(date.today() + timedelta(days=400)).isoformat(), item=new)
        self.assertNotIn("slow_before_expiry", self.codes(inv_id)[1])


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('#qaQ');
  await p.fill('#qaQ', 'Panadol');
  await p.waitForSelector('#qaRes [data-qa]');
  await p.press('#qaQ', 'Enter');                       // picks the first result, focus on the quantity
  const out = { f1: await p.evaluate(() => document.activeElement.id) };
  await p.keyboard.type('3'); await p.keyboard.press('Enter');
  out.f2 = await p.evaluate(() => document.activeElement.id);
  await p.focus('#qaExp'); await p.keyboard.type('112028');
  out.see = await p.textContent('#qaExpSee');
  await p.keyboard.press('Enter');
  out.f3 = await p.evaluate(() => document.activeElement.id);
  await p.keyboard.type('B9'); await p.keyboard.press('Enter');  // the last box adds the line
  await p.waitForSelector('table.lines tbody tr');
  await p.waitForFunction(() => document.activeElement && document.activeElement.id === 'qaQ');
  out.rows = await p.$$eval('table.lines tbody tr', (r) => r.length);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestQuickAddKeyboard(APICase):
    def test_enter_walks_the_boxes_and_adds(self):
        from dataclasses import replace
        from app import config
        from app.purchasing.service import Purchasing
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        inv_id = p.create_manual(self.ctx)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, inv_id)
        self.assertEqual((out["f1"], out["f2"], out["f3"]), ("qaQty", "qaBonus", "qaBatch"))
        self.assertIn("11/2028", out["see"])
        self.assertEqual(out["rows"], 1)


class TestDuplicates(PurchaseBase):
    def add(self, inv_id, **kw):
        line = {"item_id": self.item, "qty": 2, "public_price": "10", "discount_pct": "20", "unit_net_price": "8",
                "batch_no": "B1", "expiry_date": (date.today() + timedelta(days=700)).isoformat(), **kw}
        return self.p.add_line(self.ctx, inv_id, line, None)

    def issue(self, d, code):
        return next((i for i in d["issues"] if i["code"] == code), None)

    def test_kinds_and_merge(self):
        inv_id = self.draft(qty=3, expiry=(date.today() + timedelta(days=700)).isoformat())
        d = self.add(inv_id)
        dup = self.issue(d, "duplicate_line")
        self.assertTrue(dup["details"]["mergeable"])
        d = self.p.merge_lines(self.ctx, inv_id, dup["details"]["other_line"], dup["line_id"], None)
        self.assertEqual([(l["qty"], l["bonus_qty"]) for l in d["lines"]], [(5, 0)])
        self.assertIsNone(self.issue(d, "duplicate_line"))

    def test_other_expiry_is_information_only(self):
        inv_id = self.draft(qty=3, expiry=(date.today() + timedelta(days=700)).isoformat())
        d = self.add(inv_id, expiry_date=(date.today() + timedelta(days=800)).isoformat(), batch_no="B2")
        i = self.issue(d, "same_item_other_expiry")
        self.assertEqual(i["level"], "info")
        from app.core.errors import ValidationFailed
        with self.assertRaises(ValidationFailed):                    # two real lots are never merged
            self.p.merge_lines(self.ctx, inv_id, i["details"]["other_line"], i["line_id"], None)

    def test_no_batch_wording_and_price_difference_not_mergeable(self):
        inv_id = self.p.create_manual(self.ctx)
        exp = (date.today() + timedelta(days=700)).isoformat()
        self.add(inv_id, batch_no=None, expiry_date=exp)
        d = self.add(inv_id, batch_no=None, expiry_date=exp, discount_pct="25", unit_net_price="7")
        i = self.issue(d, "duplicate_line_no_batch")
        self.assertIn("من غير تشغيلة", i["message"])
        self.assertFalse(i["details"]["mergeable"])


class TestSplitExpiry(PurchaseBase):
    def test_part_of_a_line_gets_its_own_lot(self):
        exp = (date.today() + timedelta(days=700)).isoformat()
        inv_id = self.draft(qty=10, expiry=exp)
        line = self.p.get(self.ctx, inv_id)["lines"][0]
        d = self.p.split_line(self.ctx, inv_id, line["id"], 4, "1129", "B2", None)
        got = sorted((l["qty"], l["expiry_date"], l["batch_no"]) for l in d["lines"])
        self.assertEqual(got, [(4, "2029-11-30", "B2"), (6, exp, "B1")])
        self.assertEqual(sum(l["qty"] for l in d["lines"]), 10)
        from app.core.errors import ValidationFailed
        for bad in ((10, "1129"), (0, "1129"), (2, "1329"), (2, exp)):
            with self.assertRaises(ValidationFailed):
                self.p.split_line(self.ctx, inv_id, line["id"], bad[0], bad[1], None, None)
