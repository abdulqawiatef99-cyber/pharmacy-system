"""Label and migration review regressions, with synthetic catalog and archive data."""
import sqlite3
from contextlib import closing
from decimal import Decimal

from app.catalog import service as catalog
from app.core import people
from app.dataimport.estock_archive import NAME
from app.inventory import service as inventory
from app.printing import labels, service as printing
from app.purchasing.service import Purchasing
from app.sales.service import Sales
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase, H
from tests.test_label_fields import bars_in, decode_c128

SHORT = "07423"
GTIN = "1000000000009"
STAFF_PW = "Fixture-Pass-2026"


class _ReviewCase(APICase):
    def setUp(self):
        super().setUp()
        self.http_login()

    def make_item(self, name, *, code=None, source="import", qty=0, pack=1):
        item = catalog.create_item(self.db, self.ctx, {"name_en": name, "code": code,
                                   "pack_size": pack, "public_price": 10000}, source=source)
        if qty:
            with self.db.transaction():
                inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=qty,
                                        movement_type="opening", unit_cost=Decimal("5"))
        return item


class ReviewRoutes(_ReviewCase):
    def test_label_encoding_display_and_exact_search(self):
        item = self.make_item("Synthetic Amber 20TAB", code=SHORT, qty=4)
        self.make_item(f"Synthetic numeric distractor {SHORT}", source="manual", qty=2)
        catalog.add_barcode(self.db, self.ctx, item, GTIN)
        page = self.c.get("/labels", params={"items": f"{item}:1"})
        self.assertIn(f"<span>{SHORT}</span>", page.text)
        settings = self.c.get("/api/settings/receipt").json()
        self.assertEqual(settings["label_barcode_source"], "internal")
        digits_off = self.c.put("/api/settings/receipt", json={"label_fields": {"digits": False, "code": True}}, headers=H)
        self.assertEqual(digits_off.status_code, 200)
        self.assertIn(f"<span>{SHORT}</span>", self.c.get("/labels", params={"items": f"{item}:1"}).text)
        data = printing.label_data(self.db, self.ctx, catalog.get_item(self.db, self.ctx, item), settings)
        scanned = decode_c128(bars_in(labels.render_label(data, 38, 25)[0])[0])
        self.assertEqual(scanned, SHORT)
        for stock in ("only", ""):
            for query in (scanned, SHORT):
                matches = self.c.get("/api/items", params={"q": query, "stock": stock, "meta": 1}).json()
                self.assertEqual([r["id"] for r in matches["items"]], [item])
        saved = self.c.put("/api/settings/receipt", json={"label_barcode_source": "international",
                           "label_fields": {"digits": True, "code": False}}, headers=H)
        self.assertEqual(saved.json()["label_barcode_source"], "international")
        self.assertIn(f"<span>{GTIN}</span>", self.c.get("/labels", params={"items": f"{item}:1"}).text)

    def test_pack_review_is_ranked_by_cost_and_requires_current_pack_and_reason(self):
        low = self.make_item("Synthetic Mint 20TAB", qty=1)
        high = self.make_item("Synthetic Blue 30CAPS", qty=9)
        self.make_item("Synthetic Manual 10 AMP", source="manual", qty=30)
        self.make_item("Synthetic Known 24TAB", qty=10, pack=2)
        self.make_item("Synthetic 500 MG", qty=20)
        url = "/api/estock/pack-review"
        rows = self.c.get(url).json()["items"]
        self.assertEqual([(r["id"], r["stock_value"]) for r in rows], [(high, 4500), (low, 500)])
        fix = f"/api/items/{high}/pack-fix"
        payload = {"pack_size": 3, "expected_pack": 1, "base_unit_name": "شريط", "reason": ""}
        self.assertEqual(self.c.post(fix, json=payload, headers=H).status_code, 422)
        payload["reason"] = "Verified physical pack"
        self.assertEqual(self.c.post(fix, json=payload, headers=H).status_code, 200)
        item = self.c.get(f"/api/items/{high}/card").json()
        self.assertEqual((item["pack_size"], item["base_unit_name"], item["stock_qty"]), (3, "شريط", 27))
        payload["pack_size"] = 2
        self.assertEqual(self.c.post(fix, json=payload, headers=H).status_code, 409)
        self.assertEqual([r["id"] for r in self.c.get(url).json()["items"]], [low])

    def test_stale_pack_review_cannot_override_whole_box_confirmation(self):
        item = self.make_item("Synthetic Grey 10 AMP", qty=3)
        path = f"/api/estock/pack-review/{item}"
        whole = {"pack_size": 1, "reason": "Physical pack verified"}
        headers = {**H, "Idempotency-Key": "fixture-whole-pack"}
        self.assertEqual(self.c.post(path, json=whole, headers=headers).status_code, 200)
        replay = self.c.post(path, json=whole, headers=headers)
        self.assertEqual(replay.headers.get("Idempotent-Replay"), "true")
        change = {"pack_size": 10, "base_unit_name": "أمبول", "reason": "Stale tab"}
        self.assertEqual(self.c.post(path, json=change, headers=H).status_code, 409)
        self.assertEqual(self.c.get(f"/api/items/{item}/card").json()["stock_qty"], 3)
        self.assertEqual(self.c.get("/api/estock/pack-review").json()["items"], [])

    def test_customer_review_uses_archive_id_for_name_evidence_and_requires_owner(self):
        customer = Sales(self.db).create_customer(self.ctx, name="عميل تجريبي")
        history_only = Sales(self.db).create_customer(self.ctx, name="Second Fixture")
        archive = self.tmp / NAME
        with closing(sqlite3.connect(archive)) as con, con:
            con.executescript("""
            CREATE TABLE meta(key TEXT, value TEXT);
            CREATE TABLE map_customers(estock_id TEXT, program_id TEXT);
            CREATE TABLE e_Customer(customer_id TEXT, customer_code TEXT, customer_name_ar TEXT, customer_name_en TEXT);
            CREATE TABLE e_Sales_header(sales_id TEXT, customer_id TEXT, customer_name TEXT);
            INSERT INTO e_Customer VALUES ('81','QA81','عميل تجريبي','Fixture Renamed'),('82','QA82','','Second Fixture');
            INSERT INTO e_Sales_header VALUES ('1','81.0','Fixture Old'),('2','0','Unlinked'),('3','82','Second Former');
            """)
            con.execute("INSERT INTO meta VALUES ('org_id',?)", (self.ctx.org_id,))
            con.executemany("INSERT INTO map_customers VALUES (?,?)", [("81", customer), ("82", history_only)])
        original = archive.read_bytes()
        url = "/api/estock/customer-name-review"
        rows = {r["customer_id"]: r for r in self.c.get(url).json()["customers"]}
        self.assertEqual(set(rows[customer]["history"]), {"Fixture Old"})
        self.assertEqual(rows[customer]["name_en"], "Fixture Renamed")
        self.assertEqual(rows[history_only]["history"], ["Second Former"])
        apply = f"{url}/{customer}"
        self.assertEqual(self.c.post(apply, json={"name": "Unlinked", "current_name": rows[customer]["current_name"]},
                                     headers=H).status_code, 422)
        response = self.c.post(apply, json={"name": "Fixture Renamed", "current_name": rows[customer]["current_name"]},
                               headers=H)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.c.get(f"/api/customers/{customer}").json()["name"], "Fixture Renamed")
        keep = f"{url}/{history_only}"
        current = rows[history_only]["current_name"]
        payload = {"name": current, "current_name": current}
        headers = {**H, "Idempotency-Key": "fixture-name-review"}
        self.assertEqual(self.c.post(keep, json=payload, headers=headers).status_code, 200)
        self.assertEqual(self.c.post(keep, json=payload, headers=headers).headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.c.post(keep, json={"name": "Second Former", "current_name": current},
                                     headers=H).status_code, 409)
        self.assertEqual(self.c.get(url).json()["customers"], [])
        self.assertEqual(archive.read_bytes(), original)
        people.add_user(self.db, self.ctx, username="reviewstaff", full_name="Fixture staff",
                        password=STAFF_PW, role_code="assistant")
        self.c.cookies.clear()
        self.http_login("reviewstaff", STAFF_PW)
        self.assertEqual(self.c.get(url).status_code, 403)
        self.assertEqual(self.c.get("/api/estock/pack-review").status_code, 403)
        self.assertEqual(self.c.post(apply, json={"name": "Fixture Old"}, headers=H).status_code, 403)


BROWSER = r"""
const { chromium } = require("playwright");
(async () => {
  const [base, user, pw, staff, invoice, code, packItem] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + "/#/items");
  await p.waitForSelector("#itemsBody");
  let started, release;
  const seen = new Promise(resolve => { started = resolve; });
  const held = new Promise(resolve => { release = resolve; });
  await p.route("**/api/users/" + staff, async route => {
    started();
    await held;
    await route.continue();
  });
  const loaded = p.waitForResponse(r => r.url().endsWith("/api/users/" + staff));
  await p.evaluate(id => { location.hash = "#/users/" + id; }, staff);
  await seen;
  await p.evaluate(() => { location.hash = "#/items"; });
  await p.waitForFunction(() => document.querySelector("#itemsBody")?.children.length > 0);
  release();
  await (await loaded).finished();
  await p.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const activeItems = await p.locator("#itemsBody").count();
  await p.evaluate(id => { location.hash = "#/purchases/" + id; }, invoice);
  await p.waitForFunction(() => document.querySelector('[data-h="supplier_id"]')?.options.length > 1);
  const warehouseBonus = await p.locator("#qaBonus").isVisible();
  await p.locator("#qaQ").fill(code);
  await p.locator("#qaQ").press("Enter");
  await p.waitForFunction(() => document.querySelector("#qaPicked")?.textContent.includes("العبوة"), null, {timeout: 3000})
    .catch(() => {});
  const picked = await p.locator("#qaQ").inputValue();
  await p.locator("#qaQty").fill("1");
  await p.locator("#qaQty").press("Enter");
  const warehouseNext = await p.evaluate(() => document.activeElement.id);
  const savedBonus = await p.locator('input[data-field="bonus_qty"]').first().inputValue();
  const companyOption = p.locator('[data-h="supplier_id"] option').filter({hasText: "Fixture Company"});
  await p.locator('[data-h="supplier_id"]').selectOption(await companyOption.getAttribute("value"));
  await p.waitForFunction(() => document.querySelector("#qaBonus")?.getClientRects().length > 0, null, {timeout: 3000})
    .catch(() => {});
  const companyBonus = await p.locator("#qaBonus").isVisible();
  await p.evaluate(() => { location.hash = "#/pos"; });
  await p.locator("#posQ").fill(code);
  await p.locator("#posQ").press("Enter");
  const packAction = await p.locator("#posCart").getByRole("button", {name: "العلبة فيها كام شريط؟"})
    .waitFor({timeout: 3000}).then(() => true, () => false);
  const cart = await p.locator("#posCart").innerText();
  await p.evaluate(id => { location.hash = "#/items/" + id; }, packItem);
  await p.locator("#cardPackFix").waitFor();
  await p.evaluate(() => { location.hash = "#/pack-review"; });
  const review = p.locator(`form[data-item="${packItem}"]`);
  await review.locator('[name="size"]').fill("3");
  await review.locator('[name="unit"]').fill("شريط");
  await review.locator('[name="reason"]').fill("Fixture pack checked");
  await review.locator('button').click();
  await review.waitFor({state: "detached"});
  console.log(JSON.stringify({activeItems, warehouseBonus, warehouseNext, savedBonus, companyBonus, picked, packAction, cart}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_browser
class ReviewBrowser(_ReviewCase):
    def test_late_navigation_warehouse_entry_and_short_code(self):
        staff = people.add_user(self.db, self.ctx, username="navstaff", full_name="Fixture Staff",
                                password=STAFF_PW, role_code="assistant")
        catalog.create_supplier(self.db, self.ctx, name="Fixture Company", supplier_type="company")
        warehouse = catalog.create_supplier(self.db, self.ctx, name="Fixture Warehouse", supplier_type="warehouse")
        purchasing = Purchasing(self.db, self.s)
        draft = purchasing.create_manual(self.ctx, supplier_id=warehouse)
        item = self.make_item("Synthetic Violet 10TAB", code=SHORT, qty=8)
        purchasing.add_line(self.ctx, draft, {"item_id": item, "qty": 1, "bonus_qty": 2,
                                              "public_price": 10000, "expiry_date": "2032-01-01"}, None)
        pack_item = self.make_item("Synthetic Lime 30CAPS", qty=4)
        result = run_node(self, BROWSER, "owner", OWNER_PW, staff, draft, SHORT, pack_item)
        self.assertEqual(result["activeItems"], 1, result)
        self.assertFalse(result["warehouseBonus"], result)
        self.assertTrue(result["companyBonus"], result)
        self.assertEqual(result["warehouseNext"], "qaPrice", result)
        self.assertEqual(result["savedBonus"], "2", result)
        self.assertEqual(result["picked"], "Synthetic Violet 10TAB", result)
        self.assertIn("Synthetic Violet 10TAB", result["cart"], result)
        self.assertTrue(result["packAction"], result)
        self.assertEqual(catalog.get_item(self.db, self.ctx, pack_item)["pack_size"], 3)
