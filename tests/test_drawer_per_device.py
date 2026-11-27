"""Synthetic browser/device and upgrade acceptance for drawer isolation."""
from dataclasses import replace
from pathlib import Path
import shutil
from unittest.mock import patch

from PIL import Image
from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.catalog import service as catalog
from app.core import accounts, notify
from app.db import Database, MIGRATIONS_DIR
from app.finance import money
from app.purchasing.service import Purchasing
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import run_node
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

SETTINGS = "/api/settings/registers"
UNBOUND = "الجهاز ده مش متربط بدرج — اربطه من الإعدادات"
STAFF_PW = "Synthetic-Staff-830"


class TestDrawerPerDevice(Base):
    def setUp(self):
        super().setUp()
        settings = replace(config.load(), data_dir=self.tmp, upload_dir=self.tmp / "up",
                           backup_dir=self.tmp / "backups", allowed_hosts=("main-fixture",))
        app = create_app(self.db, settings, Purchasing(self.db, settings))
        self.main = TestClient(app, client=("127.0.0.1", 41000), base_url="http://127.0.0.1")
        self.sub = TestClient(app, base_url="http://main-fixture:8765", client=("192.0.2.2", 41001))
        self.fresh = TestClient(app, base_url="http://main-fixture:8765", client=("192.0.2.3", 41002))
        for client in (self.main, self.sub, self.fresh):
            self.login_browser(client)

    def login_browser(self, client, username="owner", password=OWNER_PW):
        self.assertEqual(client.post("/api/auth/login", json={"username": username, "password": password},
                                     headers=H).status_code, 200)

    def request(self, client, method, path, data=None, status=200, **headers):
        response = client.request(method, path, json=data, headers={**H, **headers})
        self.assertEqual(response.status_code, status, response.text)
        return response

    def add_sub(self, **settings):
        return self.request(self.main, "POST", SETTINGS, {"name": "الجهاز الفرعي", **settings}, 201).json()["id"]

    def bind(self, client, register_id):
        return self.request(client, "POST", SETTINGS + "/bind", {"register_id": register_id})

    def open(self, client, opening=10000):
        return self.request(client, "POST", "/api/cash/open", {"opening_cash": opening}, 201).json()["id"]

    def error(self, client, path, data, code, status=409):
        result = self.request(client, "POST", path, data, status).json()["error"]
        self.assertEqual(result["code"], code)
        return result

    def sell_browser(self, client):
        sid = self.s.create(self.ctx)
        sale = self.s.add_line(self.ctx, sid, self.pan, units=2)
        decisions = {str(i): "ok" for i, _ in enumerate(self.s.safety_check(self.ctx, sid))}
        return self.request(client, "POST", f"/api/sales/{sid}/complete", {
            "payments": [{"method": "cash", "amount": sale["total"]}], "safety_decisions": decisions
        }).json()

    def test_upgrade_moves_existing_open_shift_to_register_one(self):
        old_dir = self.tmp / "old-migrations"
        old_dir.mkdir()
        for f in MIGRATIONS_DIR.glob("*.sql"):
            if int(f.name[:4]) <= 45:
                shutil.copyfile(f, old_dir / f.name)
        old = Database(self.tmp / "old.db")
        self.addCleanup(old.close)
        old.migrate(old_dir)
        accounts.create_organization(old, name="Synthetic", owner_username="fixture",
                                     owner_full_name="Fixture", owner_password=OWNER_PW)
        org = old.one("SELECT id FROM organizations")["id"]
        branch = old.one("SELECT id FROM branches WHERE org_id=?", (org,))["id"]
        with old.transaction():
            old.execute("INSERT INTO cash_shifts (id, org_id, branch_id, opening_cash, opened_by, opened_at)"
                        " VALUES ('legacy', ?, ?, 1925, 'fixture', '2026-01-01T00:00:00Z')", (org, branch))
        self.assertIn("0050_registers", old.migrate())
        row = old.one("SELECT s.id, s.opening_cash, r.name FROM cash_shifts s JOIN registers r"
                      " ON r.org_id=s.org_id AND r.branch_id=s.branch_id AND r.id=s.register_id")
        self.assertEqual(dict(row), {"id": "legacy", "opening_cash": 1925, "name": "الجهاز الرئيسي"})
        self.assertEqual(old.all("PRAGMA foreign_key_check"), [])

    def test_unbound_lan_browser_can_browse_but_cannot_touch_cash(self):
        self.open(self.main)
        self.request(self.fresh, "GET", "/api/items")
        error = self.error(self.fresh, "/api/cash/movements", {"kind": "deposit", "amount": 100, "note": "تجربة"},
                           "device_unbound")
        self.assertEqual(error["message"], UNBOUND)
        self.assertEqual(self.db.all("SELECT id FROM cash_movements"), [])

    def test_binding_sets_private_cookie_rotates_and_requires_owner_or_manager(self):
        sub_id = self.add_sub()
        cookie = self.bind(self.sub, sub_id).headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("samesite=strict", cookie.lower())
        self.assertGreaterEqual(len(cookie.split("=", 1)[1].split(";", 1)[0]), 43)
        self.bind(self.fresh, sub_id)
        self.error(self.sub, "/api/cash/open", {"opening_cash": 0}, "device_unbound")
        accounts.add_user(self.db, self.ctx, username="assistant", full_name="Synthetic", role_code="assistant",
                          password=STAFF_PW)
        self.login_browser(self.fresh, "assistant", STAFF_PW)
        self.request(self.fresh, "POST", SETTINGS + "/bind", {"register_id": 1}, 403)
        self.request(self.fresh, "PATCH", f"{SETTINGS}/{sub_id}", {"name": "ممنوع"}, 403)
        accounts.add_user(self.db, self.ctx, username="manager", full_name="Synthetic", role_code="manager",
                          password=STAFF_PW)
        self.login_browser(self.fresh, "manager", STAFF_PW)
        self.request(self.fresh, "PATCH", f"{SETTINGS}/{sub_id}", {"name": "درج تاني"})
        self.bind(self.fresh, sub_id)

    def test_two_open_shifts_and_no_cash_without_this_shift_or_deactivation_while_open(self):
        main_shift = self.open(self.main)
        sub_id = self.add_sub()
        self.bind(self.sub, sub_id)
        self.error(self.sub, "/api/cash/movements", {"kind": "deposit", "amount": 100, "note": "تجربة"},
                   "no_open_shift")
        sub_shift = self.open(self.sub)
        self.assertNotEqual(main_shift, sub_shift)
        self.error(self.sub, "/api/cash/open", {"opening_cash": 0}, "shift_already_open")
        self.request(self.main, "PATCH", f"{SETTINGS}/{sub_id}", {"active": False}, 409)
        self.assertEqual(self.request(self.sub, "GET", "/api/cash/current").json()["id"], sub_shift)

    def test_cash_sale_refund_void_payout_deposit_supplier_and_collection_use_request_drawer(self):
        main_shift = self.open(self.main)
        self.bind(self.sub, self.add_sub())
        sub_shift = self.open(self.sub)
        returned = self.sell_browser(self.main)
        voided = self.sell_browser(self.main)
        self.sell_browser(self.sub)
        self.request(self.sub, "POST", f"/api/sales/{returned['id']}/return", {
            "lines": {returned["lines"][0]["id"]: 1}, "refund_method": "cash", "reason": "مرتجع تجريبي"
        })
        self.request(self.sub, "POST", f"/api/sales/{voided['id']}/void", {"reason": "إلغاء تجريبي"})
        alerts = [n for n in notify.listing(self.db, self.ctx)["items"] if n["kind"] in ("return", "void")]
        self.assertEqual({n["kind"] for n in alerts}, {"return", "void"})
        self.assertTrue(all("الجهاز الفرعي" in str(n) for n in alerts))
        for kind in ("payout", "deposit"):
            self.request(self.sub, "POST", "/api/cash/movements", {"kind": kind, "amount": 100, "note": "حركة تجربة"})
        supplier = catalog.create_supplier(self.db, self.ctx, name="Synthetic supplier")
        self.request(self.sub, "POST", f"/api/suppliers/{supplier}/pay",
                     {"method": "drawer", "amount": 100, "note": "دفعة تجريبية"})
        customer = self.s.create_customer(self.ctx, name="Synthetic customer", credit_limit=12000)
        self.sell(credit=4000, customer=customer)
        self.request(self.sub, "POST", f"/api/customers/{customer}/payments", {"amount": 100})
        posted = [(r["shift_id"], r["kind"]) for r in self.db.all(
            "SELECT shift_id, kind FROM cash_movements ORDER BY rowid")]
        self.assertEqual(posted, [(main_shift, "sale")] * 2 + [(sub_shift, kind) for kind in
                         ("sale", "refund", "void", "payout", "deposit", "payout", "customer_payment")])

    def test_cross_register_void_does_not_erase_the_original_register_sale(self):
        main_shift = self.open(self.main)
        self.bind(self.sub, self.add_sub())
        sub_shift = self.open(self.sub)
        sale = self.sell_browser(self.main)
        self.request(self.sub, "POST", f"/api/sales/{sale['id']}/void", {"reason": "إلغاء تجريبي"})
        first = self.request(self.main, "GET", f"/api/cash/shifts/{main_shift}").json()
        second = self.request(self.sub, "GET", f"/api/cash/shifts/{sub_shift}").json()
        self.assertEqual((first["sales_count"], first["sales_total"], first["voided_count"]), (1, sale["total"], 0))
        self.assertEqual((first["cash_by_kind"]["sale"], second["cash_by_kind"]["void"]), (sale["total"], -sale["total"]))
        self.assertEqual((second["sales_count"], second["voided_count"]), (0, 1))
        closed = self.request(self.main, "POST", "/api/cash/close", {"counted_cash": 14000, "shift_id": main_shift}).json()
        self.assertEqual((closed["sales_count"], closed["sales_total"]), (1, sale["total"]))

    def test_count_is_scoped_and_only_management_can_close_another_drawer_to_the_same_treasury(self):
        main_shift = self.open(self.main)
        sub_id = self.add_sub()
        self.bind(self.sub, sub_id)
        sub_shift = self.open(self.sub)
        self.request(self.sub, "POST", "/api/cash/movements", {"kind": "deposit", "amount": 500, "note": "تجربة إيداع"})
        accounts.add_user(self.db, self.ctx, username="assistant", full_name="Synthetic", role_code="assistant",
                          password=STAFF_PW)
        self.login_browser(self.sub, "assistant", STAFF_PW)
        self.error(self.sub, "/api/cash/close", {"counted_cash": 10000, "shift_id": main_shift},
                   "other_register_shift", status=403)
        self.assertEqual(self.request(self.sub, "GET", "/api/cash/current").json()["id"], sub_shift)
        result = self.request(self.main, "POST", "/api/cash/close", {
            "counted_cash": 10500, "shift_id": sub_shift, "float_left": 10000})
        self.assertEqual(result.json()["register_name"], "الجهاز الفرعي")
        self.assertEqual(self.request(self.main, "GET", "/api/cash/current").json()["id"], main_shift)
        treasury = [a for a in money.accounts(self.db, self.ctx) if a["kind"] == "treasury"]
        self.assertEqual([a["balance"] for a in treasury], [500])
        self.request(self.main, "PATCH", f"{SETTINGS}/{sub_id}", {"active": False})

    def test_cash_replay_key_cannot_show_or_write_a_different_drawer(self):
        self.open(self.main)
        self.bind(self.sub, self.add_sub())
        self.open(self.sub)
        body = {"kind": "deposit", "amount": 100, "note": "حركة تجربة"}
        self.request(self.main, "POST", "/api/cash/movements", body, **{"Idempotency-Key": "register-fixture"})
        self.request(self.sub, "POST", "/api/cash/movements", body, 422, **{"Idempotency-Key": "register-fixture"})
        self.assertEqual(self.db.one("SELECT count(*) n FROM cash_movements")["n"], 1)

    def test_receipt_drawer_and_labels_use_the_bound_printer_and_kick_setting(self):
        from app.printing import settings as printing_settings
        printing_settings.save(self.db, self.ctx, {"receipt_target": "win:Fixture Main",
                                                     "label_lang": "tspl", "label_target": "win:Fixture Main Labels"})
        sub_id = self.add_sub(receipt_printer=r"win:\\Fixture\Receipts", label_printer="win:Fixture Labels", drawer_kick=False)
        self.bind(self.sub, sub_id)
        self.open(self.main)
        self.open(self.sub)
        with patch("app.printing.service.receipt_image", return_value=Image.new("1", (8, 8))), \
             patch("app.printing.service.escpos.send") as send, \
             patch("app.printing.labels.render_label", return_value=(Image.new("1", (8, 8)), [])):
            sale = self.sell_browser(self.main)
            self.request(self.sub, "POST", f"/api/print/receipt/{sale['id']}", {})
            self.request(self.sub, "POST", "/api/print/labels", {"items": [{"id": self.pan, "count": 1}]})
            self.error(self.sub, "/api/print/drawer", {"reason": "cash_check"}, "drawer_kick_disabled")
            self.request(self.main, "POST", "/api/print/drawer", {"reason": "cash_check"})
            targets = [call.args[0] for call in send.call_args_list]
        self.assertIn(r"win:\\Fixture\Receipts", targets)
        self.assertIn("win:Fixture Labels", targets)
        self.assertEqual(targets[-1], "win:Fixture Main")

    def test_secondary_register_does_not_pulse_the_inherited_main_printer(self):
        from app.printing import settings as printing_settings

        printing_settings.save(self.db, self.ctx, {"receipt_target": "win:Fixture Main"})
        sub_id = self.add_sub()
        self.bind(self.sub, sub_id)
        self.open(self.sub)
        with patch("app.printing.service.escpos.send") as send:
            sale = self.sell_browser(self.sub)
            self.assertEqual(sale["devices"]["drawer"], "none")
            send.assert_not_called()
        self.request(self.main, "PATCH", f"{SETTINGS}/{sub_id}", {"drawer_kick": True}, 422)
        self.request(self.main, "PATCH", f"{SETTINGS}/{sub_id}",
                     {"receipt_printer": "win:fixture main", "drawer_kick": True}, 422)
        self.request(self.main, "PATCH", f"{SETTINGS}/{sub_id}",
                     {"receipt_printer": "win:Fixture Sub", "drawer_kick": True})
        with patch("app.printing.service.escpos.send") as send:
            sale = self.sell_browser(self.sub)
            self.assertEqual(sale["devices"]["drawer"], "sent")
            self.assertEqual(send.call_args.args[0], "win:Fixture Sub")

    def test_shift_drawer_totals_and_lan_guide_identify_register(self):
        self.open(self.main, 10000)
        self.bind(self.sub, self.add_sub())
        self.open(self.sub, 20000)
        current = self.request(self.sub, "GET", "/api/cash/current").json()
        self.assertEqual((current["register_name"], current["expected_cash"]), ("الجهاز الفرعي", 20000))
        shifts = self.request(self.main, "GET", "/api/cash/shifts").json()
        self.assertEqual({s["register_name"] for s in shifts["shifts"]}, {"الجهاز الرئيسي", "الجهاز الفرعي"})
        self.assertEqual(shifts["drawer_totals"]["balance"], 30000)
        info = self.request(self.sub, "GET", SETTINGS).json()
        self.assertIn("http://main-fixture:8765", info["lan_urls"])
        self.assertEqual(info["allowed_hosts"], ["main-fixture"])
        source = (Path(__file__).parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("تشغيل جهاز فرعي", source)
        self.assertIn("هذا الجهاز", source)


@needs_browser
class TestRegisterBrowser(APICase):
    def test_two_browser_profiles_link_and_open_separate_drawers(self):
        result = run_node(self, r"""
const { chromium } = require('playwright');
(async () => {
  const [base, password] = process.argv.slice(1);
  const browser = await chromium.launch();
  try {
    async function signIn(page) {
      await page.goto(base + '/');
      await page.evaluate(async (pw) => {
        const reply = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' },
          body: JSON.stringify({ username: 'owner', password: pw })
        });
        if (!reply.ok) throw new Error('Login failed');
      }, password);
      await page.goto(base + '/#/registers');
      await page.locator('#registerBind').waitFor({ timeout: 8000 });
    }
    async function bind(page, name) {
      await page.locator('#registerBind select').selectOption({ label: name });
      await page.locator('#registerBind button').click();
      await page.getByText('تم ربط الجهاز', { exact: true }).waitFor({ timeout: 8000 });
    }
    async function shift(page, name, opening) {
      await page.goto(base + '/#/shift');
      await page.locator('#shOpen input').fill(opening);
      await page.locator('#shOpen button').click();
      await page.getByRole('heading', { name: 'الشيفت المفتوح — ' + name }).waitFor({ timeout: 8000 });
    }

    const main = await browser.newPage();
    const sub = await browser.newPage();
    await signIn(main);
    await bind(main, 'الجهاز الرئيسي');
    await main.getByText('أضف درج جديد', { exact: true }).click();
    const form = main.locator('form[data-register=""]');
    await form.locator('[name=name]').fill('الجهاز الفرعي');
    await form.locator('button').click();
    await main.locator('#registerBind option').filter({ hasText: 'الجهاز الفرعي' }).waitFor({ state: 'attached' });

    await signIn(sub);
    await sub.getByText('الجهاز ده مش متربط بدرج — اربطه من الإعدادات', { exact: true }).waitFor();
    await sub.getByRole('heading', { name: 'تشغيل جهاز فرعي' }).waitFor();
    await bind(sub, 'الجهاز الفرعي');
    await Promise.all([shift(main, 'الجهاز الرئيسي', '100'), shift(sub, 'الجهاز الفرعي', '200')]);

    const info = await Promise.all([main, sub].map((p) => p.evaluate(async () => {
      const r = await fetch('/api/cash/current');
      return { shift: await r.json(), cookieVisible: document.cookie.includes('pharm_register=') };
    })));
    console.log(JSON.stringify(info));
  } finally {
    await browser.close();
  }
})().catch((err) => { console.error(err); process.exit(1); });
""", OWNER_PW)
        self.assertEqual([x["shift"]["opening_cash"] for x in result], [10000, 20000])
        self.assertEqual(len({x["shift"]["register_id"] for x in result}), 2)
        self.assertFalse(any(x["cookieVisible"] for x in result))
