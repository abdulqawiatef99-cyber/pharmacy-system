"""QA-29 PR2: the purchase invoice screen (independent tester, synthetic data, develop 95416e7).

4. Quick-add row: qty 5 + expiry 1130 + Enter added nothing and said nothing. Reproduced: the first-run tour opened
   400 ms after the screen and moved the focus to its «التالي» button while the person typed (the expiry box kept
   "11", the Enter pressed «التالي»). Without the tour, Enter at the end of the row added a line with no price and
   the only sign was a list item far from the row. Now: the tour never takes the focus; adding with a required box
   empty marks it red next to the row with an Arabic message, and adds nothing.
5. Native browser messages were English («Please fill out this field») on Arabic screens: now Arabic, in red on the
   field itself.
6. Approve from two tabs: the second tab kept «مسودة»; now it reloads and says «معتمدة بالفعل». An approved invoice
   no longer shows «مشكلة قبل الاعتماد». One money unit on the screen (ج), never «قرش».
7. Manual create twice left an empty (cancelled) draft: no draft is created for a refused number, and a second
   empty draft for the same supplier by the same person is the first one.
"""
from datetime import date, timedelta

from app.catalog import service as cat
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase, H

EXP = (date.today() + timedelta(days=800)).isoformat()

QUICK = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('#qaQ');
  await p.focus('#qaQ');
  await p.keyboard.type('QA29PAN', { delay: 120 });                   // a person typing while the tour starts
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.activeElement && document.activeElement.id === 'qaQty');
  await p.keyboard.type('5', { delay: 120 }); await p.keyboard.press('Enter');
  await p.keyboard.type('1130', { delay: 120 });
  await p.waitForTimeout(600);
  out.exp = await p.$eval('#qaExp', (i) => i.value);
  out.tourShown = await p.evaluate(() => !!document.querySelector('#tourBub'));
  out.overlap = await p.evaluate(() => {
    const b = document.querySelector('#tourBub'), a = document.activeElement;
    if (!b || !a) return false;
    const x = b.getBoundingClientRect(), y = a.getBoundingClientRect();
    return !(x.right <= y.left || y.right <= x.left || x.bottom <= y.top || y.bottom <= x.top);
  });
  await p.keyboard.press('Enter');
  out.afterExp = await p.evaluate(() => document.activeElement.id);
  await p.click('#qaAdd');
  await p.waitForTimeout(800);
  const lines = () => p.$$eval('table.lines tbody tr[data-purchase-line], table.lines tbody tr', (x) => x.filter((r) => r.querySelector('input[data-field="qty"]')).length);
  out.lines1 = await lines();
  out.err = await p.$eval('#qaErr', (e) => e.textContent.trim()).catch(() => '');
  out.invalid = await p.$$eval('#qa [aria-invalid="true"]', (x) => x.map((i) => i.id));
  out.focus1 = await p.evaluate(() => document.activeElement.id);
  await p.keyboard.type('10', { delay: 60 });
  out.invalidAfterTyping = await p.$$eval('#qa [aria-invalid="true"]', (x) => x.map((i) => i.id));
  await p.click('#qaAdd');
  await p.waitForFunction(() => [...document.querySelectorAll('table.lines input[data-field="qty"]')].length === 1, null, { timeout: 5000 }).catch(() => {});
  out.lines2 = await lines();
  out.errAfter = await p.$eval('#qaErr', (e) => e.textContent.trim()).catch(() => '');
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""

NATIVE = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  await p.goto(base + '/#/purchases');
  await p.waitForSelector('#manForm');
  await p.evaluate(() => { window.__native = []; document.querySelectorAll('#manForm :is(input, select)').forEach((i) =>
    i.addEventListener('invalid', (e) => window.__native.push(e.defaultPrevented ? 'arabic' : 'native'))); });
  await p.click('#manForm button[type=submit]');
  await p.waitForTimeout(500);
  out.toast = await p.$eval('#toast', (t) => t.hidden ? '' : t.textContent.trim());
  out.native = await p.evaluate(() => window.__native);
  out.messages = await p.$$eval('#manForm [aria-invalid="true"]', (x) => x.map((i) => i.title));
  out.marked = await p.$$eval('#manForm [aria-invalid="true"]', (x) => x.length);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""

TWO_TABS = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv, flag] = process.argv.slice(1);
""" + LOGIN + r"""
  const fs = require('fs');
  p.on('dialog', async (d) => { await d.accept(); });
  const out = {};
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('#approve');
  fs.writeFileSync(flag + '/open', '1');                               // the other tab approves now
  for (let k = 0; k < 100 && !fs.existsSync(flag + '/done'); k++) await p.waitForTimeout(100);
  await p.click('#approve');
  await p.waitForFunction(() => !document.querySelector('#approve'), null, { timeout: 5000 }).catch(() => {});
  out.badge = await p.$eval('h1 .badge', (e) => e.textContent.trim());
  out.toast = await p.$eval('#toast', (t) => t.textContent.trim());
  out.text = await p.$eval('#app', (e) => e.innerText);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""

SHOW = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('table.lines');
  console.log(JSON.stringify({ text: await p.$eval('#app', (e) => e.innerText) }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


class Base(APICase):
    def setUp(self):
        super().setUp()
        self.it = cat.create_item(self.db, self.ctx, {"name_en": "QA29 Synthetic", "pack_size": 2,
                                                      "public_price": 4000, "code": "QA29PAN"})
        self.sup = cat.create_supplier(self.db, self.ctx, name="QA29 Synthetic Supplier")
        self.p = Purchasing(self.db, self.s)

    def draft(self, no="QA29-1", line=True):
        inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        self.p.update_header(self.ctx, inv, {"invoice_no": no}, None)
        if line:
            self.p.add_line(self.ctx, inv, {"item_id": self.it, "qty": 2, "public_price": 4000, "discount_bp": 2000,
                                            "batch_no": "B1", "expiry_date": EXP}, None)
        return inv


@needs_browser
class TestQuickAddRow(Base):
    def test_tour_never_takes_the_focus_and_missing_boxes_show_red_next_to_the_row(self):
        inv = self.draft(line=False)
        o = run_node(self, QUICK, "owner", OWNER_PW, inv)
        self.assertEqual(o["exp"], "1130", "the tour took the focus while the expiry was typed")
        self.assertFalse(o["overlap"], "the tour bubble covers the box being typed in")
        self.assertEqual(o["afterExp"], "qaDisc")                          # Enter goes on to the next box
        self.assertEqual(o["lines1"], 0)
        self.assertIn("الخصم", o["err"])
        self.assertIn("qaDisc", o["invalid"])
        self.assertEqual(o["focus1"], "qaDisc")
        self.assertEqual(o["invalidAfterTyping"], [])
        self.assertEqual(o["lines2"], 1)
        self.assertEqual(o["errAfter"], "")


@needs_browser
class TestArabicFieldMessages(Base):
    def test_required_fields_say_it_in_arabic_on_the_field(self):
        o = run_node(self, NATIVE, "owner", OWNER_PW)
        self.assertTrue(o["native"], o)
        self.assertEqual(set(o["native"]), {"arabic"}, "the browser showed its own English bubble")
        self.assertTrue(o["messages"], o)
        for m in o["messages"] + [o["toast"]]:
            self.assertNotRegex(m, r"[A-Za-z]{3,}", m)
        self.assertTrue(o["toast"], o)                                    # an Arabic message is on screen
        self.assertGreaterEqual(o["marked"], 1)


@needs_browser
class TestTwoTabsAndApprovedScreen(Base):
    def test_second_tab_reloads_and_says_already_approved(self):
        import os
        import time
        inv = self.draft()

        def other_tab(flags):
            for _ in range(200):
                if os.path.exists(os.path.join(flags, "open")):
                    break
                time.sleep(0.05)
            v = self.p.get(self.ctx, inv)["invoice"]["version"]
            self.p.approve(self.ctx, inv, v)
            open(os.path.join(flags, "done"), "w").close()

        import tempfile
        flags = tempfile.mkdtemp(dir=self.tmp)
        o = run_node(self, TWO_TABS, "owner", OWNER_PW, inv, flags, behind=lambda: other_tab(flags))
        self.assertEqual(o["badge"], "معتمدة")
        self.assertIn("معتمدة بالفعل", o["toast"])
        self.assertNotIn("قبل الاعتماد", o["text"])

    def test_one_money_unit_on_the_screen(self):
        inv = self.draft()
        self.p.update_header(self.ctx, inv, {"declared_total": 6405}, None)    # 5 piastres off the computed 6400
        o = run_node(self, SHOW, "owner", OWNER_PW, inv)
        self.assertNotIn("قرش", o["text"])
        self.assertIn("الفرق 0.05 ج", o["text"])


class TestNoEmptyDrafts(Base):
    def _count(self):
        return self.db.one("SELECT count(*) n FROM purchase_invoices WHERE org_id=?", (self.ctx.org_id,))["n"]

    def test_a_refused_number_creates_no_draft(self):
        self.http_login()
        body = {"supplier_id": self.sup, "invoice_no": "QA29-DUP"}
        first = self.c.post("/api/purchases", json=body, headers=H)
        self.assertEqual(first.status_code, 201, first.text)
        again = self.c.post("/api/purchases", json=body, headers=H)          # a second click, a new key
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(again.json()["error"]["details"]["invoice_id"], first.json()["invoice"]["id"])
        self.assertEqual(self._count(), 1, "a cancelled empty draft was left behind")

    def test_a_second_empty_draft_for_the_same_supplier_is_the_first_one(self):
        self.http_login()
        a = self.c.post("/api/purchases", json={"supplier_id": self.sup}, headers=H)
        b = self.c.post("/api/purchases", json={"supplier_id": self.sup}, headers=H)
        self.assertEqual(b.json()["invoice"]["id"], a.json()["invoice"]["id"])
        self.assertEqual(self._count(), 1)
        # a draft with lines, or another supplier, is never reused
        self.p.add_line(self.ctx, a.json()["invoice"]["id"], {"item_id": self.it, "qty": 1, "public_price": 4000,
                                                              "discount_bp": 0, "expiry_date": EXP}, None)
        c = self.c.post("/api/purchases", json={"supplier_id": self.sup}, headers=H)
        self.assertNotEqual(c.json()["invoice"]["id"], a.json()["invoice"]["id"])

    def test_screen_disables_the_start_button_while_it_creates(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        part = js[js.index('$("#manForm").addEventListener("submit"'):][:900]
        self.assertTrue("disabled = true" in part, "a double click sent two creates")
