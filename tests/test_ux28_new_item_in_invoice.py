"""UX-28 PR2 (owner 23/11 and 26/11): a new item is added from inside the purchase invoice. When the searched item is not
found, «+ صنف جديد» opens a short card in place (names AR/EN, barcode, pack and its units, public price, group); saving
creates the item and puts it straight on the invoice's entry row. An existing line's item card opens and is edited
without leaving the invoice. Permissions: items.write (price: items.price). Refusals refuse the whole item. Synthetic data."""
from datetime import date, timedelta

from app.catalog import service as cat
from app.core import people
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import H, APICase

PW = "Staff-Pass-2026"
EXPIRY = (date.today() + timedelta(days=700)).isoformat()


def new_item(**kw):
    item = {"name_en": "SYNTHOCLAV 1G TAB", "name_ar": "سينثوكلاف", "pack_size": 2, "pack_unit_name": "علبة",
            "base_unit_name": "شريط", "public_price": 9550}
    item.update(kw)
    return item


class TestRefusals(APICase):
    def post(self, item, barcodes=(), force=False, category_id=None):
        body = {"item": item, "barcodes": list(barcodes), "force": force}
        if category_id is not None:
            body["category_id"] = category_id
        return self.c.post("/api/items", json=body, headers=H)

    def category(self, name, no_return=False):
        from app.db import new_id
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO categories (id, org_id, name, no_return) VALUES (?,?,?,?)",
                            (cid, self.ctx.org_id, name, int(no_return)))
        return cid

    def count(self):
        return self.db.one("SELECT count(*) n FROM items WHERE org_id=?", (self.ctx.org_id,))["n"]

    def test_owner_creates_with_every_short_card_field(self):
        self.http_login()
        cat_id = self.category("Synthetic Group A")
        r = self.post(new_item(), ["6221234567890"], category_id=cat_id)
        self.assertEqual(r.status_code, 201, r.text)
        it = r.json()
        self.assertEqual((it["name_en"], it["name_ar"], it["pack_size"], it["base_unit_name"], it["public_price"]),
                         ("SYNTHOCLAV 1G TAB", "سينثوكلاف", 2, "شريط", 9550))
        self.assertEqual(it["category_id"], cat_id)
        self.assertEqual([b["code"] if isinstance(b, dict) else b for b in it["barcodes"]], ["6221234567890"])

    def test_assistant_without_items_write_is_refused(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="Synthetic Assistant", password=PW,
                        role_code="assistant")
        self.http_login("asst", PW)
        before = self.count()
        self.assertEqual(self.post(new_item()).status_code, 403)
        self.assertEqual(self.count(), before)

    def test_pharmacist_creates_but_not_with_a_price(self):
        people.add_user(self.db, self.ctx, username="pharma1", full_name="Synthetic Pharmacist", password=PW,
                        role_code="pharmacist")
        self.http_login("pharma1", PW)
        self.assertEqual(self.post(new_item()).status_code, 403)                 # price needs items.price
        before = self.count()
        # a no-return group changes what can be returned: the owner's right, and the whole item is refused
        self.assertEqual(self.post(new_item(public_price=None), category_id=self.category("Synthetic No-Return Group", True)).status_code, 403)
        self.assertEqual(self.count(), before)
        r = self.post(new_item(public_price=None), category_id=self.category("Synthetic Group B"))
        self.assertEqual(r.status_code, 201, r.text)

    def test_unknown_group_refuses_the_whole_item(self):
        self.http_login()
        before = self.count()
        self.assertEqual(self.post(new_item(), category_id="no-such-group").status_code, 404)
        self.assertEqual(self.count(), before)

    def test_no_name_bad_pack_bad_price_are_refused(self):
        self.http_login()
        before = self.count()
        for bad in (new_item(name_en="", name_ar=""), new_item(pack_size=0), new_item(pack_size=1.5),
                    new_item(public_price=-1)):
            r = self.post(bad)
            self.assertIn(r.status_code, (400, 422), (bad, r.text))
        self.assertEqual(self.count(), before)

    def test_barcode_of_another_item_refuses_the_whole_item(self):
        self.http_login()
        cat.add_barcode(self.db, self.ctx, self.pan, "6221234567890")
        before = self.count()
        r = self.post(new_item(), ["6221234567890"])
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["error"]["code"], "barcode_taken")
        self.assertEqual(self.count(), before)                                   # no half-made item left behind

    def test_near_duplicate_asks_then_force_creates(self):
        self.http_login()
        r = self.post(new_item(name_en="Panadol 500mg 24 tab", name_ar=None, pack_size=2, public_price=4000))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "possible_duplicate")
        r = self.post(new_item(name_en="Panadol 500mg 24 tab", name_ar=None, pack_size=3, public_price=4000), force=True)
        self.assertEqual(r.status_code, 201, r.text)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, draft, expiry] = process.argv.slice(1);
""" + LOGIN + r"""
  p.on('dialog', (d) => d.accept());
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  const out = {};
  await p.goto(base + '/#/purchases/' + draft);
  await p.waitForSelector('#qaQ');
  // not found: the button is offered right there
  await p.fill('#qaQ', 'Synthoclav');
  await p.waitForSelector('#qaRes [data-qa-new]');
  out.offeredWhenMissing = true;
  await p.click('#qaRes [data-qa-new]');
  await p.waitForSelector('dialog.new-item-card[open]');
  out.prefilled = await p.inputValue('dialog.new-item-card [name=name_en]');
  await p.fill('dialog.new-item-card [name=name_ar]', 'سينثوكلاف');
  await p.fill('dialog.new-item-card [name=barcode]', '6221234567890');
  await p.fill('dialog.new-item-card [name=pack_size]', '2');
  await p.fill('dialog.new-item-card [name=public_price]', '95.50');
  const created = p.waitForResponse((r) => r.url().endsWith('/api/items') && r.request().method() === 'POST');
  await p.press('dialog.new-item-card [name=public_price]', 'Enter');
  out.status = (await created).status();
  await p.waitForSelector('dialog.new-item-card', { state: 'detached' });
  // the new item is the picked item of the entry row; qty, expiry, Enter puts it on the invoice
  out.picked = await p.inputValue('#qaQ');
  out.price = await p.inputValue('#qaPrice');
  out.focus = await p.evaluate(() => document.activeElement.id);
  await p.fill('#qaQty', '3');
  await p.fill('#qaExp', expiry);
  const added = p.waitForResponse((r) => r.url().endsWith('/lines') && r.request().method() === 'POST');
  await p.click('#qaAdd');
  out.line = JSON.parse((await added).request().postData()).line;
  await p.waitForSelector('[data-purchase-card]');
  // the line's item card opens and edits without leaving the invoice
  await p.click('[data-purchase-card]');
  await p.waitForSelector('dialog.invoice-item-card [data-card-edit]');
  await p.click('dialog.invoice-item-card [data-card-edit]');
  await p.fill('dialog.invoice-item-card [name=name_en]', 'SYNTHOCLAV 1 G TAB');
  const saved = p.waitForResponse((r) => /\/api\/items\/[^/]+$/.test(r.url()) && ['PATCH', 'PUT'].includes(r.request().method()));
  await p.click('dialog.invoice-item-card #itemForm button[type=submit], dialog.invoice-item-card #itemForm button:not([type=button])');
  out.editStatus = (await saved).status();
  out.stillOnInvoice = await p.evaluate(() => location.hash);
  // a scanned barcode no item has: the same button, the barcode filled in
  await p.waitForSelector('dialog[open]', { state: 'detached' });
  await p.fill('#qaQ', '');
  await p.evaluate(() => document.querySelector('#qa').dispatchEvent(new CustomEvent('purchase-scan', { detail: '6229999999990' })));
  await p.waitForSelector('#qaRes [data-qa-new]');
  await p.dblclick('#qaRes [data-qa-new]');                     // review P2: a double click opens one card
  await p.waitForSelector('dialog.new-item-card[open]');
  await p.waitForTimeout(300);
  out.cards = await p.$$eval('dialog.new-item-card', (ds) => ds.length);
  out.scanBarcode = await p.inputValue('dialog.new-item-card [name=barcode]');
  out.scanName = await p.inputValue('dialog.new-item-card [name=name_en]');
  await p.keyboard.press('Escape');
  await p.waitForSelector('dialog.new-item-card', { state: 'detached' });
  // review P1: a GS1 code: the card gets the GTIN, the line the code's expiry and batch
  await p.evaluate(() => document.querySelector('#qa').dispatchEvent(new CustomEvent('purchase-scan', { detail: '01062212345678971728123110LOT7' })));
  await p.waitForSelector('#qaRes [data-qa-new]');
  await p.click('#qaRes [data-qa-new]');
  await p.waitForSelector('dialog.new-item-card[open]');
  out.gs1Barcode = await p.inputValue('dialog.new-item-card [name=barcode]');
  out.gs1Name = await p.inputValue('dialog.new-item-card [name=name_en]');
  await p.fill('dialog.new-item-card [name=name_en]', 'SYNTHO GS1 DROPS');
  await p.press('dialog.new-item-card [name=name_en]', 'Enter');
  await p.waitForSelector('dialog.new-item-card', { state: 'detached' });
  out.gs1Batch = await p.inputValue('#qaBatch');
  out.gs1Exp = await p.inputValue('#qaExp');
  // Codex P2: an alphanumeric scan is the barcode, never the English name
  await p.evaluate(() => document.querySelector('#qa').dispatchEvent(new CustomEvent('purchase-scan', { detail: 'ABC12345' })));
  await p.waitForSelector('#qaRes [data-qa-new]');
  await p.click('#qaRes [data-qa-new]');
  await p.waitForSelector('dialog.new-item-card[open]');
  out.alnum = [await p.inputValue('dialog.new-item-card [name=barcode]'), await p.inputValue('dialog.new-item-card [name=name_en]')];
  await p.keyboard.press('Escape');
  await p.waitForSelector('dialog.new-item-card', { state: 'detached' });
  // Codex P2: leaving the screen while the card is still opening does not lock the button for good
  await p.route('**/api/categories*', () => {});                 // held for ever, as an abandoned request is
  await p.fill('#qaQ', 'Nothingsuch');
  await p.waitForSelector('#qaRes [data-qa-new]');
  await p.click('#qaRes [data-qa-new]');
  await p.evaluate(() => { location.hash = '#/purchases'; });
  await p.waitForSelector('#manForm');
  await p.unroute('**/api/categories*');
  await p.evaluate((d) => { location.hash = '#/purchases/' + d; }, draft);
  await p.waitForSelector('#qaQ');
  await p.fill('#qaQ', 'Nothingsuch');
  await p.waitForSelector('#qaRes [data-qa-new]');
  await p.click('#qaRes [data-qa-new]');
  out.reopened = await p.waitForSelector('dialog.new-item-card[open]', { timeout: 5000 }).then(() => true, () => false);
  await p.keyboard.press('Escape');
  // the same box scanned again (another batch) now finds the item
  await p.evaluate(() => document.querySelector('#qa').dispatchEvent(new CustomEvent('purchase-scan', { detail: '01062212345678971729063010LOT8' })));
  await p.waitForFunction(() => document.querySelector('#qaQ').value.includes('SYNTHO GS1'));
  out.rescan = await p.inputValue('#qaBatch');
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""

ASSIST = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, draft] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  await p.goto(base + '/#/purchases/' + draft);
  await p.waitForSelector('#qaQ');
  await p.fill('#qaQ', 'Synthoclav');
  await p.waitForFunction(() => document.querySelector('#qaRes').textContent.trim().length > 0);
  console.log(JSON.stringify({ offered: (await p.$('#qaRes [data-qa-new]')) !== null }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScreen(APICase):
    def draft(self):
        sup = cat.create_supplier(self.db, self.ctx, name="Synthetic New-Item Supplier")
        purch = Purchasing(self.db, self.s)
        inv = purch.create_manual(self.ctx, supplier_id=sup)
        purch.update_header(self.ctx, inv, {"invoice_no": "UX28-NEW", "invoice_date": date.today().isoformat()}, None)
        return inv

    def test_new_item_card_in_place_then_on_the_line_then_edited(self):
        inv = self.draft()
        out = run_node(self, SCRIPT, "owner", OWNER_PW, inv, EXPIRY)
        self.assertTrue(out["offeredWhenMissing"])
        self.assertEqual(out["prefilled"], "SYNTHOCLAV")
        self.assertEqual(out["status"], 201)
        item = self.db.one("SELECT * FROM items WHERE name_ar='سينثوكلاف'")
        self.assertEqual((item["pack_size"], item["public_price"]), (2, 9550))
        self.assertIn("SYNTHOCLAV", out["picked"])
        self.assertEqual(out["price"], "95.50")
        self.assertEqual(out["focus"], "qaQty")
        self.assertEqual(out["line"]["item_id"], item["id"])
        self.assertEqual(out["line"]["qty"], 3)
        self.assertEqual(out["editStatus"], 200)
        self.assertEqual(out["stillOnInvoice"], "#/purchases/" + inv)
        self.assertEqual(self.db.one("SELECT name_en FROM items WHERE id=?", (item["id"],))["name_en"], "SYNTHOCLAV 1 G TAB")
        self.assertEqual(out["scanBarcode"], "6229999999990")
        self.assertEqual((out["cards"], out["scanName"]), (1, ""))
        self.assertIn(out["gs1Barcode"], ("06221234567897", "6221234567897"))
        self.assertEqual(out["gs1Name"], "")
        self.assertEqual(out["gs1Batch"], "LOT7")
        self.assertTrue(out["gs1Exp"])
        self.assertEqual(out["rescan"], "LOT8")
        self.assertEqual(out["alnum"], ["ABC12345", ""])
        self.assertTrue(out["reopened"])

    def test_no_button_without_items_write(self):
        inv = self.draft()
        people.add_user(self.db, self.ctx, username="asst", full_name="Synthetic Assistant", password=PW,
                        role_code="assistant")
        out = run_node(self, ASSIST, "asst", PW, inv)
        self.assertFalse(out["offered"])
