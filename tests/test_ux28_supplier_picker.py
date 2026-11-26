"""UX-28 PR1 (owner's live notes 26/11): every supplier picker is a type-to-search box instead of a long dropdown.
Arabic (أ/ا, ة/ه, ى/ي folded) or English, the phone, or the same keys on the other keyboard language; recent suppliers
first. Keyboard: type, Up/Down, Enter. The <select> stays underneath as the value holder, so forms and change handlers
are unchanged. Synthetic data."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.catalog import service as cat
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def _block(start, end):
    i = JS.index(start)
    return JS[i:JS.index(end, i)]


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestRanking(unittest.TestCase):
    LIST = [
        {"id": "a", "name": "ابن سينا فارما", "phone": "01001234567", "last_used": "2026-11-20T10:00:00Z"},
        {"id": "b", "name": "الشركة المتحدة", "phone": None, "last_used": "2026-11-25T10:00:00Z"},
        {"id": "c", "name": "Pharma Overseas", "phone": "0225551234", "last_used": None},
        {"id": "d", "name": "مخزن الأمل", "phone": None, "last_used": "2026-11-26T09:00:00Z", "inactive": True},
        {"id": "e", "name": "أسيوط للأدوية", "phone": None, "last_used": None},
    ]

    def rank(self, q):
        src = _block("// UX-28 supplier search: begin", "// UX-28 supplier search: end") + (
            f"console.log(JSON.stringify(supRank({json.dumps(self.LIST)}, {json.dumps(q)}).map((s) => s.id)));")
        r = subprocess.run([shutil.which("node"), "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_empty_query_lists_recent_first_and_stopped_last(self):
        self.assertEqual(self.rank(""), ["b", "a", "e", "c", "d"])

    def test_arabic_letters_are_folded(self):
        self.assertEqual(self.rank("ابن"), ["a"])
        self.assertEqual(self.rank("اسيوط"), ["e"])          # typed without the hamza
        self.assertEqual(self.rank("المتحده"), ["b"])        # ه for ة

    def test_english_any_case_and_words_in_any_order(self):
        self.assertEqual(self.rank("overseas PHA"), ["c"])

    def test_phone_digits_also_in_arabic_numerals(self):
        self.assertEqual(self.rank("1234567"), ["a"])
        self.assertEqual(self.rank("٠٢٢٥٥٥"), ["c"])

    def test_other_keyboard_language(self):
        self.assertEqual(self.rank("hfk sdkh"), ["a"])          # «ابن سينا» typed on the English layout
        self.assertEqual(self.rank("حاشقةش"), ["c"])            # «pharma» typed on the Arabic layout

    def test_start_of_name_before_inside_then_recent(self):
        # «ا» starts «ابن» and «الشركة» and «اسيوط»; «مخزن الأمل» has it inside a later word; stopped stays last
        self.assertEqual(self.rank("ا"), ["b", "a", "e", "d"])


class TestLastUsed(APICase):
    def test_suppliers_carry_their_last_invoice_time_in_this_org_only(self):
        purch = Purchasing(self.db, self.s)
        used = cat.create_supplier(self.db, self.ctx, name="Synthetic Used Supplier")
        cat.create_supplier(self.db, self.ctx, name="Synthetic Idle Supplier")
        purch.create_manual(self.ctx, supplier_id=used)
        gone = cat.create_supplier(self.db, self.ctx, name="Synthetic Cancelled Supplier")
        purch.cancel(self.ctx, purch.create_manual(self.ctx, supplier_id=gone), "اتلغت تجربة", None)
        self.http_login()
        rows = {r["name"]: r for r in self.c.get("/api/suppliers").json()}
        self.assertTrue(rows["Synthetic Used Supplier"]["last_used"])
        self.assertIsNone(rows["Synthetic Idle Supplier"]["last_used"])
        self.assertIsNone(rows["Synthetic Cancelled Supplier"]["last_used"])    # a cancelled upload is not "recent"


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  await p.goto(base + '/#/purchases');
  await p.waitForSelector('#manForm .spIn');
  out.selectHidden = await p.evaluate(() => getComputedStyle(document.querySelector('#manForm [name=supplier_id]')).opacity === '0');
  await p.click('#manForm .spIn');
  out.firstOnFocus = await p.textContent('#manForm .spList .spOpt');
  await p.fill('#manForm .spIn', 'اسيوط');
  await p.waitForFunction(() => document.querySelectorAll('#manForm .spList .spOpt').length === 1);
  await p.keyboard.press('Enter');
  out.manual = await p.evaluate(() => document.querySelector('#manForm [name=supplier_id]').selectedOptions[0].text);
  out.shown = await p.inputValue('#manForm .spIn');
  // the report filter: «الكل» first, Down then Enter picks the next
  await p.click('#puRep .spIn');
  await p.fill('#puRep .spIn', 'pharma');
  await p.keyboard.press('ArrowDown');
  await p.keyboard.press('ArrowUp');
  const changed = p.waitForResponse((r) => r.url().includes('/api/purchases?') && r.url().includes('supplier='));
  await p.keyboard.press('Enter');
  await changed;
  out.filter = await p.evaluate(() => document.querySelector('#puSup').selectedOptions[0].text);
  // Codex P2: the chosen supplier ranks below the first 15 (20 recent ones above it): Enter with nothing typed keeps it
  await p.click('#puRep .spIn');
  await p.fill('#puRep .spIn', 'اسيوط');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.querySelector('#puSup').selectedOptions[0].text === 'أسيوط للأدوية');
  await p.click('body');
  await p.focus('#puRep .spIn');
  out.listedFirstOnOpen = await p.textContent('#puRep .spOpt.on');
  await p.keyboard.press('Enter');
  out.keptLowRanked = await p.evaluate(() => document.querySelector('#puSup').selectedOptions[0].text);
  await p.click('#puRep .spIn');
  await p.fill('#puRep .spIn', 'pharma');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.querySelector('#puSup').selectedOptions[0].text === 'Pharma Overseas');
  // Esc puts back what was chosen
  await p.click('#puRep .spIn');
  await p.fill('#puRep .spIn', 'xyz');
  await p.keyboard.press('Escape');
  out.afterEsc = await p.inputValue('#puRep .spIn');
  // a required picker left empty: the form is not sent and the box gets the focus
  await p.fill('#manForm [name=invoice_no]', 'SYN-1');
  await p.evaluate(() => { const s = document.querySelector('#manForm [name=supplier_id]'); s.value = ''; s.dispatchEvent(new Event('change')); });
  await p.click('#manForm button[type=submit]');
  await p.waitForTimeout(200);
  out.focusAfterEmpty = await p.evaluate(() => document.activeElement.classList.contains('spIn'));
  out.stillHere = await p.evaluate(() => location.hash);
  // the draft invoice header: picking a supplier saves it
  await p.fill('#manForm .spIn', 'ابن');
  await p.keyboard.press('Enter');
  await p.click('#manForm button[type=submit]');
  await p.waitForFunction(() => /#\/purchases\/./.test(location.hash));
  await p.waitForSelector('.spIn[data-for="supplier_id"]');
  await p.waitForFunction(() => document.querySelector('.spIn[data-for="supplier_id"]').value.includes('ابن سينا'));
  const saved = p.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/api/purchases/'));
  await p.fill('.spIn[data-for="supplier_id"]', 'متحد');
  await p.keyboard.press('Enter');
  out.patch = JSON.parse((await saved).request().postData());
  await p.waitForFunction(() => document.querySelector('.spIn[data-for="supplier_id"]')?.value.includes('المتحدة'));
  // review P1: coming back to the box and pressing Enter with nothing typed keeps the chosen supplier (no save)
  let extra = 0;
  p.on('request', (r) => { if (r.method() === 'PATCH' && r.url().includes('/api/purchases/')) extra++; });
  await p.click('body');
  await p.focus('.spIn[data-for="supplier_id"]');
  await p.keyboard.press('Enter');
  await p.waitForTimeout(300);
  out.enterKeeps = [extra, await p.evaluate(() => document.querySelector('[data-h="supplier_id"]').selectedOptions[0].text)];
  // review P1: a click on the caption lands in the box, never in the hidden list (its type-ahead would save)
  await p.click('body');
  await p.evaluate(() => document.querySelector('[data-h="supplier_id"]').closest('label').firstChild.parentElement.click());
  out.captionFocus = await p.evaluate(() => document.activeElement.classList.contains('spIn'));
  await p.keyboard.type('ph');
  await p.waitForTimeout(300);
  out.typedNoSave = extra;
  // supplier return screen
  await p.goto(base + '/#/supplier-returns/new');
  await p.fill('.spIn[data-for="srSup"]', 'overseas');
  await p.keyboard.press('Enter');
  out.returns = await p.evaluate(() => document.querySelector('#srSup').selectedOptions[0].text);
  // the suppliers page: type, Enter opens the account (to pay or see the statement)
  await p.goto(base + '/#/suppliers');
  await p.fill('.spIn[data-for="supGo"]', '01001234');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => /#\/suppliers\/./.test(location.hash));
  out.account = await p.evaluate(() => location.hash);
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScreen(APICase):
    def setUp(self):
        super().setUp()
        cat.create_supplier(self.db, self.ctx, name="الشركة المتحدة")
        cat.create_supplier(self.db, self.ctx, name="Pharma Overseas")
        with self.db.transaction():
            self.db.execute("UPDATE suppliers SET phone='01001234567' WHERE name='ابن سينا فارما'")
        purch = Purchasing(self.db, self.s)
        for n in range(20):                    # 20 recent suppliers rank above the chosen one
            purch.create_manual(self.ctx, supplier_id=cat.create_supplier(self.db, self.ctx, name=f"Synthetic Recent {n:02d}"))
        cat.create_supplier(self.db, self.ctx, name="أسيوط للأدوية")

    def test_type_arrows_enter(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertTrue(out["selectHidden"])
        self.assertTrue(out["firstOnFocus"])
        self.assertEqual(out["manual"], "أسيوط للأدوية")
        self.assertEqual(out["shown"], "أسيوط للأدوية")
        self.assertEqual(out["filter"], "Pharma Overseas")
        self.assertEqual(out["keptLowRanked"], "أسيوط للأدوية")
        self.assertIn("أسيوط للأدوية", out["listedFirstOnOpen"])
        self.assertEqual(out["afterEsc"], "Pharma Overseas")
        self.assertTrue(out["focusAfterEmpty"])
        self.assertEqual(out["stillHere"], "#/purchases")
        sup = self.db.one("SELECT id FROM suppliers WHERE name='الشركة المتحدة'")["id"]
        self.assertEqual(out["patch"]["changes"], {"supplier_id": sup})
        self.assertEqual(out["enterKeeps"][0], 0)
        self.assertIn("المتحدة", out["enterKeeps"][1])
        self.assertTrue(out["captionFocus"])
        self.assertEqual(out["typedNoSave"], 0)
        self.assertEqual(out["returns"], "Pharma Overseas")
        self.assertEqual(out["account"], "#/suppliers/" + self.db.one("SELECT id FROM suppliers WHERE name='ابن سينا فارما'")["id"])


class TestEveryPicker(unittest.TestCase):
    def test_no_supplier_select_left_without_the_search_box(self):
        # every place that lists suppliers in a <select> wraps it with supPicker
        for call in ('"#upForm [name=supplier_id]", "#manForm [name=supplier_id]", "#puSup"].forEach((q) => supPicker($(q)))',
                     "supPicker(sel)", 'supPicker($("#siF [name=sup]"))', 'supPicker($("#srSup"))', 'supPicker($("#prSupSel"))',
                     'querySelectorAll("#prBody .prSup").forEach(supPicker)', 'supPicker($("#supGo"))'):
            self.assertIn(call, JS, call)



if __name__ == "__main__":
    unittest.main()
