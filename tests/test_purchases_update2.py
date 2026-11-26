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
        self.assertEqual(parse_expiry("1128", compact=True), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("112028", compact=True), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("11/28"), ("2028-11-30", "month"))
        for bad in ("1328", "2027", "128", "11280"):
            self.assertEqual(parse_expiry(bad, compact=True), (None, None), bad)


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
        line = self.p.get(self.ctx, inv_id)["lines"][0]
        self.p.update_line(self.ctx, inv_id, line["id"], {"public_price": 1000, "unit_net_price": 800}, None)
        d, issues = self.codes(inv_id)
        i = issues["slow_before_expiry"]
        self.assertIn("هتبيع حوالي", i["message"])
        self.assertEqual(i["details"]["qty"], 300)
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
  // A cashier who already completed first-run guidance: the tour would take the focus mid-typing.
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('#qaQ');
  await p.fill('#qaQ', 'Panadol');
  await p.waitForSelector('#qaRes [data-qa]');
  await p.press('#qaQ', 'Enter');                       // picks the first result, focus on the quantity
  const out = { f1: await p.evaluate(() => document.activeElement.id) };
  await p.keyboard.type('3'); await p.keyboard.press('Enter');
  out.f2 = await p.evaluate(() => document.activeElement.id);
  await p.keyboard.type('112028');
  await p.waitForFunction(() => document.querySelector('#qaExpSee').textContent.includes('11/2028'));
  out.see = await p.textContent('#qaExpSee');
  await p.keyboard.press('Enter');
  out.f3 = await p.evaluate(() => document.activeElement.id);
  for (const field of ['qaNet', 'qaBonus', 'qaBatch']) {
    await p.keyboard.press('Enter');
    await p.waitForFunction(id => document.activeElement.id === id, field);
  }
  await p.keyboard.type('B9'); await p.keyboard.press('Enter');
  for (const field of ['qaMargin', 'qaTax', 'qaTaxMode']) {
    await p.keyboard.press('Enter');
    await p.waitForFunction(id => document.activeElement.id === id, field);
  }
  await p.keyboard.press('Enter');  // the last box adds the line
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
        self.assertEqual((out["f1"], out["f2"], out["f3"]), ("qaQty", "qaExp", "qaDisc"))
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


LABELS = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = { asked: [], posts: 0 };
  p.on('dialog', async (d) => { out.asked.push(d.message()); await d.dismiss(); });
  p.on('request', (r) => { if (r.url().includes('/api/print/labels')) out.posts++; });
  await p.goto(base + '/#/purchases/' + inv);
  await p.click('#invLabels');
  await p.waitForSelector('dialog [data-i]');
  out.checked = await p.$$eval('dialog [data-i]', (c) => c.filter((x) => x.checked).length);
  await p.focus('dialog [data-n="0"]'); await p.keyboard.press('Enter');          // Enter never prints
  await p.click('dialog [data-all="1"]');
  await p.click('dialog [data-go="1"]');                                          // the confirm is dismissed
  await p.waitForTimeout(200);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestLabelsAfterApproval(APICase):
    def test_nothing_ticked_enter_never_prints_confirm_counts(self):
        from dataclasses import replace
        from app import config
        from app.purchasing.service import Purchasing
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        sup = cat.create_supplier(self.db, self.ctx, name="مورد ملصقات")
        inv_id = p.create_manual(self.ctx)
        p.update_header(self.ctx, inv_id, {"supplier_id": sup, "invoice_no": "L1"}, None)
        p.add_line(self.ctx, inv_id, {"item_id": self.pan, "qty": 3, "bonus_qty": 1, "public_price": "4000",
                                      "discount_pct": "20", "unit_net_price": "3200", "batch_no": "L",
                                      "expiry_date": (date.today() + timedelta(days=900)).isoformat()}, None)
        p.approve(self.ctx, inv_id, None)
        out = run_node(self, LABELS, "owner", OWNER_PW, inv_id)
        self.assertEqual(out["checked"], 0)
        self.assertEqual(out["posts"], 0)
        self.assertEqual(out["asked"], ["هتطبع 4 ملصق لـ 1 صنف؟"])


class TestImportIsAlwaysADraft(PurchaseBase):
    """U2-16: supplier files in the shapes of the big distributors (SYNTHETIC rows) come in as a DRAFT to review,
    never approved and never touching stock. (Reading a photo by AI is unchanged: off without the owner's key.)"""

    def test_nahda_xlsx_and_pharma_csv(self):
        import io
        from openpyxl import Workbook
        from app.inventory import service as invs
        cat.create_supplier(self.db, self.ctx, name="النهضة")
        wb = Workbook(); ws = wb.active
        for r in (["شركة النهضة للأدوية"], ["رقم الفاتورة", "N-100", "التاريخ", date.today().strftime("%d/%m/%Y")], [],
                  ["الصنف", "الكمية", "بونص", "سعر الجمهور", "الخصم", "الصافي", "الإجمالي", "التشغيلة", "الصلاحية"],
                  ["Guard Tab", 4, 1, 10, "20%", 8, 32, "N1", "11/2029"]):
            ws.append(r)
        b = io.BytesIO(); wb.save(b)
        csv = ("Item;Qty;Public Price;Discount;Net Price;Total;Batch;Expiry\n"
               "Guard Tab;2;10,00;0,20;8,00;16,00;P1;11/2029\n").encode("utf-8")
        for data, name in ((b.getvalue(), "nahda.xlsx"), (csv, "pharma.csv")):
            inv_id = self.p.import_file(self.ctx, data, name)
            d = self.p.get(self.ctx, inv_id)
            self.assertEqual(d["invoice"]["status"], "draft", name)
            self.assertEqual(len(d["lines"]), 1, name)
        self.assertEqual(invs.stock_for_item(self.db, self.ctx, self.item)["total_qty"], 0)


class TestReviewFixes(PurchaseBase):
    def test_split_keeps_tax_and_total_exact(self):
        inv_id = self.draft(qty=3, expiry=(date.today() + timedelta(days=700)).isoformat())
        line = self.p.get(self.ctx, inv_id)["lines"][0]
        self.p.update_line(self.ctx, inv_id, line["id"], {"tax_amount": 1, "line_total": 25}, None)
        before = self.p.get(self.ctx, inv_id)["invoice"]["computed_total"]
        d = self.p.split_line(self.ctx, inv_id, line["id"], 1, "1129", None, None)
        self.assertEqual(sum(l["tax_amount"] or 0 for l in d["lines"]), 1)
        self.assertEqual(sum(l["line_total"] or 0 for l in d["lines"]), 25)
        self.assertEqual(d["invoice"]["computed_total"], before)

    def test_acceptance_survives_a_new_day_and_new_sales(self):
        inv_id = self.draft(qty=5, expiry=(date.today() + timedelta(days=100)).isoformat())
        d = self.p.get(self.ctx, inv_id)
        i = next(x for x in d["issues"] if x["code"] == "short_expiry")
        self.assertNotIn("days", i["details"])                    # nothing in the match that changes by itself
        self.p.acknowledge(self.ctx, inv_id, "short_expiry", "متفق على مرتجع", i["line_id"], None)
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=-3, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)
        d = self.p.get(self.ctx, inv_id)
        self.assertFalse([x for x in d["issues"] if x["code"] == "short_expiry" and not x["acknowledged"]])

    def test_two_lines_of_one_item_are_counted_together(self):
        with self.db.transaction():       # 1 a day for 90 days
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=-90, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)
        exp = (date.today() + timedelta(days=150)).isoformat()   # 60 days before "near": about 60 will sell
        inv_id = self.draft(qty=50, expiry=exp)
        self.p.add_line(self.ctx, inv_id, {"item_id": self.item, "qty": 50, "public_price": "10", "discount_pct": "20",
                                           "unit_net_price": "8", "batch_no": "B1", "expiry_date": exp}, None)
        d = self.p.get(self.ctx, inv_id)
        self.assertTrue([x for x in d["issues"] if x["code"] == "slow_before_expiry"])

    def test_compact_expiry_only_for_typed_entry(self):
        self.assertEqual(parse_expiry("011228"), (None, None))         # an import cell: never a guess
        self.assertEqual(parse_expiry("1211"), (None, None))
        self.assertEqual(parse_expiry("1128", compact=True), ("2028-11-30", "month"))
        self.assertEqual(parse_expiry("011228", compact=True), (None, None))   # year 1228: refused

    def test_duplicate_found_after_another_expiry(self):
        inv_id = self.p.create_manual(self.ctx)
        e1, e2 = ((date.today() + timedelta(days=n)).isoformat() for n in (700, 800))
        for e in (e1, e2, e2):
            d = self.p.add_line(self.ctx, inv_id, {"item_id": self.item, "qty": 2, "public_price": "10", "discount_pct": "20",
                                                   "unit_net_price": "8", "batch_no": "B", "expiry_date": e}, None)
        self.assertTrue([x for x in d["issues"] if x["code"] == "duplicate_line"])


class TestCodexPayable(PurchaseBase):
    def total(self, inv_id):
        return self.p.get(self.ctx, inv_id)["invoice"]["computed_total"]

    def line(self, inv_id, **kw):
        base = {"item_id": self.item, "public_price": "1000", "discount_pct": "20", "batch_no": "B",
                "expiry_date": (date.today() + timedelta(days=700)).isoformat()}
        return self.p.add_line(self.ctx, inv_id, {**base, **kw}, None)

    def test_split_keeps_the_payable_total(self):
        inv_id = self.p.create_manual(self.ctx)
        d = self.line(inv_id, qty=2, unit_net_price="800", tax_amount="2", line_total="1601")
        before = self.total(inv_id)
        d = self.p.split_line(self.ctx, inv_id, d["lines"][0]["id"], 1, "1129", None, None)
        self.assertEqual(self.total(inv_id), before)

    def test_merge_keeps_the_payable_total(self):
        inv_id = self.p.create_manual(self.ctx)
        self.line(inv_id, qty=1, unit_net_price="800", tax_amount="8", line_total="800")      # printed without tax
        d = self.line(inv_id, qty=1, unit_net_price="800", tax_amount="8", line_total="808")  # printed with tax
        before = self.total(inv_id)
        dup = next(i for i in d["issues"] if i["code"] == "duplicate_line")
        d = self.p.merge_lines(self.ctx, inv_id, dup["details"]["other_line"], dup["line_id"], None)
        self.assertEqual(self.total(inv_id), before)
        self.assertEqual(len(d["lines"]), 1)


class TestCodexRound2(PurchaseBase):
    def test_merge_refused_while_a_value_waits_for_review(self):
        inv_id = self.p.create_manual(self.ctx)
        exp = (date.today() + timedelta(days=700)).isoformat()
        base = {"item_id": self.item, "qty": 1, "public_price": "1000", "discount_pct": "20", "unit_net_price": "800",
                "batch_no": "B", "expiry_date": exp}
        self.p.add_line(self.ctx, inv_id, base, None)
        d = self.p.add_line(self.ctx, inv_id, base, None)
        with self.db.transaction():          # as an extracted file leaves it: quantity read with low confidence
            self.db.execute("UPDATE purchase_invoice_lines SET field_sources='{\"qty\": \"file\"}',"
                            " field_confidence='{\"qty\": 0.5}' WHERE id=?", (d["lines"][-1]["id"],))
        d = self.p.get(self.ctx, inv_id)
        dup = next(i for i in d["issues"] if i["code"] == "duplicate_line")
        from app.core.errors import ValidationFailed
        with self.assertRaises(ValidationFailed):
            self.p.merge_lines(self.ctx, inv_id, dup["details"]["other_line"], dup["line_id"], None)


class TestCodexRound3(PurchaseBase):
    def sell(self, n, ctx=None):
        with self.db.transaction():
            inv.post_movement(self.db, ctx or self.ctx, item_id=self.item, qty_delta=-n, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)

    def issues(self, inv_id, ctx=None):
        d = self.p.get(ctx or self.ctx, inv_id)
        return {i["code"] for i in d["issues"] if i["level"] == "error" and not i["acknowledged"]}

    def test_exact_capacity_no_float_truncation(self):
        self.sell(13)                                               # 13 in 90 days
        inv_id = self.draft(qty=13, expiry=(date.today() + timedelta(days=180)).isoformat())   # 90 days to "near"
        self.assertNotIn("slow_before_expiry", self.issues(inv_id))

    def test_guard_uses_the_invoice_branch(self):
        from dataclasses import replace
        other = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO branches (id, org_id, name) VALUES (?,?,?)", (other, self.ctx.org_id, "فرع تاني"))
        there = replace(self.ctx, branch_id=other)
        self.sell(90)                                               # sales only in the invoice's branch
        inv_id = self.draft(qty=300, expiry=(date.today() + timedelta(days=300)).isoformat())
        self.assertIn("slow_before_expiry", self.issues(inv_id))
        self.assertIn("slow_before_expiry", self.issues(inv_id, there))   # reviewed from the other branch: same answer


class TestCodexRound4(PurchaseBase):
    def test_acceptance_does_not_follow_a_rematched_item(self):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=-90, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)
        other = cat.create_item(self.db, self.ctx, {"name_en": "Other Tab", "public_price": 1000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=other, qty_delta=-1, movement_type="sale",
                              ref_type="sale", ref_id=new_id(), allow_negative=True)
        inv_id = self.draft(qty=300, expiry=(date.today() + timedelta(days=300)).isoformat())
        d = self.p.get(self.ctx, inv_id)
        i = next(x for x in d["issues"] if x["code"] == "slow_before_expiry")
        self.p.acknowledge(self.ctx, inv_id, "slow_before_expiry", "عرض من المورد", i["line_id"], None)
        self.p.update_line(self.ctx, inv_id, i["line_id"], {"item_id": other}, None)
        d = self.p.get(self.ctx, inv_id)
        self.assertTrue([x for x in d["issues"] if x["code"] == "slow_before_expiry" and not x["acknowledged"]])

    def test_merge_with_a_missing_quantity_is_refused_cleanly(self):
        inv_id = self.p.create_manual(self.ctx)
        base = {"item_id": self.item, "qty": 1, "public_price": "1000", "discount_pct": "20", "unit_net_price": "800",
                "batch_no": "B", "expiry_date": (date.today() + timedelta(days=700)).isoformat()}
        self.p.add_line(self.ctx, inv_id, base, None)
        d = self.p.add_line(self.ctx, inv_id, base, None)
        a, b = d["lines"][0]["id"], d["lines"][1]["id"]
        d = self.p.update_line(self.ctx, inv_id, b, {"qty": None}, None)
        dup = next((x for x in d["issues"] if x["code"] == "duplicate_line"), None)
        self.assertFalse(dup and dup["details"]["mergeable"])
        from app.core.errors import ValidationFailed
        with self.assertRaises(ValidationFailed):
            self.p.merge_lines(self.ctx, inv_id, a, b, None)
