"""Synthetic money regressions from the QA-NIGHT audit in #461."""
import json
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import patch

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.catalog import service as catalog
from app.core import people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.finance import money
from app.procurement import returns
from app.purchasing import entry_settings, validation
from app.purchasing.service import Purchasing
from app.sales.cash import CashDrawer
from tests.helpers import DBTestCase, OWNER_PW
from tests.test_api import H, approval_headers
from tests.test_sale_exchange import Base as ExchangeCase


class TestDrawerOutflow(ExchangeCase):
    def http_client(self):
        settings = replace(config.load(), upload_dir=self.tmp / "up", data_dir=self.tmp,
                           backup_dir=self.tmp / "backups")
        client = TestClient(create_app(self.db, settings, Purchasing(self.db, settings)))
        login = client.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H)
        self.assertEqual(login.status_code, 200, login.text)
        return client

    def empty_next_shift(self, customer=None, **pay):
        self.drawer = CashDrawer(self.db)
        sid = self.sell(self.a, 1, customer=customer, **pay)
        self.drawer.close(self.ctx, denominations={"1000": 1} if not pay else {}, float_left=0)
        self.drawer.open(self.ctx, 0)
        return sid

    def snapshot(self):
        return tuple(self.db.one(f"SELECT count(*) FROM {table}")[0] for table in
                     ("sale_returns", "stock_movements", "cash_movements", "money_movements", "audit_log"))

    def test_refund_refuses_empty_drawer_and_succeeds_after_deposit(self):
        sid = self.empty_next_shift()
        before = self.snapshot()
        lines = {self.line(sid): 1}
        args = {"refund_method": "cash", "reason": "مرتجع تجربة"}
        with self.assertRaises(Conflict) as error:
            self.s.return_items(self.ctx, sid, lines, **args)
        self.assertEqual(error.exception.code, "drawer_negative")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.s.get(self.ctx, sid)["returned_total"], 0)
        self.drawer.movement(self.ctx, 1000, "deposit", "تمويل الدرج")
        self.assertEqual(self.s.return_items(self.ctx, sid, lines, **args)["refund"], 1000)
        self.assertEqual((self.qty(self.a), self.drawer.summary(self.ctx)["expected_cash"]), (20, 0))

    def test_void_refuses_empty_drawer_and_succeeds_after_deposit(self):
        sid = self.empty_next_shift()
        before = self.snapshot()
        with self.assertRaises(Conflict) as error:
            self.s.void(self.ctx, sid, "إلغاء تجربة")
        self.assertEqual(error.exception.code, "drawer_negative")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.s.get(self.ctx, sid)["status"], "completed")
        self.drawer.movement(self.ctx, 1000, "deposit", "تمويل الدرج")
        self.assertEqual(self.s.void(self.ctx, sid, "إلغاء تجربة")["status"], "voided")
        self.assertEqual((self.qty(self.a), self.drawer.summary(self.ctx)["expected_cash"]), (20, 0))

    def test_even_exchange_is_possible_with_empty_drawer(self):
        sid = self.empty_next_shift()
        lines = {self.line(sid): 1}
        draft = self.draft(self.a, 1)
        self.assertEqual(self.s.return_value(self.ctx, sid, lines, refund_method="cash", new_sid=draft), (1000, 1000))
        result = self.s.exchange(self.ctx, sid, lines, draft, refund_method="cash", reason="استبدال تجربة")
        self.assertEqual(result["settled"], {"pay": 0, "refund": 0})
        self.assertEqual(self.drawer.summary(self.ctx)["expected_cash"], 0)

    def test_exchange_can_improve_a_drawer_that_was_already_negative(self):
        sid = self.empty_next_shift()
        shift_id = self.drawer.summary(self.ctx)["id"]
        with self.db.transaction():
            self.db.execute("INSERT INTO cash_movements (id, org_id, shift_id, amount, kind, created_by)"
                            " VALUES ('legacy-refund-fixture', ?, ?, -600, 'refund', ?)",
                            (self.ctx.org_id, shift_id, self.ctx.user_id))
        lines = {self.line(sid): 1}
        result = self.s.exchange(self.ctx, sid, lines, self.draft(self.b, 1), refund_method="cash",
                                 reason="استبدال تجربة")
        self.assertEqual(result["settled"], {"pay": 500, "refund": 0})
        self.assertEqual(self.drawer.summary(self.ctx)["expected_cash"], -100)

    def test_cheaper_exchange_checks_only_the_cash_difference(self):
        customer = self.s.create_customer(self.ctx, name="عميل اختبار")
        sid = self.empty_next_shift(customer)
        lines = {self.line(sid): 1}
        draft = self.draft(self.c, 1)
        before = self.snapshot()
        self.assertEqual(self.s.return_value(self.ctx, sid, lines, refund_method="cash", new_sid=draft), (1000, 400))
        with self.assertRaises(Conflict) as error:
            self.s.exchange(self.ctx, sid, lines, draft, refund_method="cash", reason="استبدال تجربة")
        self.assertEqual(error.exception.code, "drawer_negative")
        self.assertEqual(error.exception.details, {"balance": 0, "amount": 600})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.s.get(self.ctx, draft)["status"], "draft")
        self.assertIsNone(self.s.get(self.ctx, draft)["customer_id"])
        self.drawer.movement(self.ctx, 600, "deposit", "تمويل الدرج")
        result = self.s.exchange(self.ctx, sid, lines, draft, refund_method="cash", reason="استبدال تجربة")
        self.assertEqual(result["settled"], {"pay": 0, "refund": 600})
        report = self.drawer.summary(self.ctx)
        self.assertEqual((report["expected_cash"], report["cash_by_kind"]["refund"]), (0, -1000))

    def test_cash_override_requires_pin_even_for_owner(self):
        sid = self.empty_next_shift(paid_card=1000)
        lines = {self.line(sid): 1}
        payload = {"lines": lines, "refund_method": "cash", "reason": "رد نقدي استثنائي"}
        before = self.snapshot()
        with self.assertRaises(Forbidden) as error:
            self.s.return_items(self.ctx, sid, lines, refund_method="cash", reason=payload["reason"])
        self.assertEqual(error.exception.details["permission"], "sales.refund_other_method")
        self.assertEqual(self.snapshot(), before)
        approver = people.add_user(self.db, self.ctx, username="manager", full_name="مدير تجربة",
                                   password=OWNER_PW, role_code="manager", pin="9753")
        client = self.http_client()
        path = f"/api/sales/{sid}/return"
        permission = "sales.refund_other_method"
        before = self.snapshot()
        headers = approval_headers(client, approver, "9753", permission, path, "POST", payload=payload)
        blocked = client.post(path, json=payload, headers=headers)
        self.assertEqual(blocked.json()["error"]["code"], "drawer_negative", blocked.text)
        self.assertEqual(self.snapshot(), before)
        self.drawer.movement(self.ctx, 1000, "deposit", "تمويل الدرج")
        headers = approval_headers(client, approver, "9753", permission, path, "POST", payload=payload)
        done = client.post(path, json=payload, headers=headers)
        self.assertEqual(done.status_code, 200, done.text)
        replay = client.post(path, json=payload, headers=headers)
        self.assertEqual(replay.headers["Idempotent-Replay"], "true")
        log = self.db.one("SELECT after_json FROM audit_log WHERE action='sale.return' AND entity_id=?", (sid,))
        self.assertEqual(json.loads(log[0])["approved_by"], approver)
        self.assertEqual(json.loads(log[0])["reason"], payload["reason"])

    def test_cash_override_requires_a_written_reason_on_both_api_routes(self):
        sid = self.empty_next_shift(paid_card=1000)
        client = self.http_client()
        payload = {"lines": {self.line(sid): 1}, "refund_method": "cash", "reason": None}
        for endpoint in ("return", "exchange"):
            with self.subTest(endpoint=endpoint):
                if endpoint == "exchange":
                    payload["new_sale_id"] = self.draft(self.a, 1)
                response = client.post(f"/api/sales/{sid}/{endpoint}", json=payload,
                                       headers={**H, "Idempotency-Key": endpoint})
                self.assertEqual(response.status_code, 422, response.text)

    def test_owner_can_confirm_a_cash_override_with_his_own_pin(self):
        sid = self.empty_next_shift(paid_card=1000)
        self.drawer.movement(self.ctx, 1000, "deposit", "تمويل الدرج")
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        client = self.http_client()
        path = f"/api/sales/{sid}/return"
        payload = {"lines": {self.line(sid): 1}, "refund_method": "cash", "reason": "رد نقدي استثنائي"}
        self.assertEqual(client.get("/api/approvers", headers=H).json()["approvers"], [])
        initial = client.post(path, json=payload, headers={**H, "Idempotency-Key": "owner-pin"})
        self.assertEqual(initial.status_code, 403, initial.text)
        candidates = client.get("/api/approvers", headers=H).json()["approvers"]
        self.assertEqual([a["id"] for a in candidates], [self.ctx.user_id])
        headers = approval_headers(client, self.ctx.user_id, "9753", "sales.refund_other_method",
                                   path, "POST", payload=payload, key="owner-pin")
        done = client.post(path, json=payload, headers=headers)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(client.get("/api/approvers", headers=H).json()["approvers"], [])


class TestShiftClose(DBTestCase):
    def test_impossible_split_refused_before_shift_or_treasury_changes(self):
        drawer = CashDrawer(self.db)
        shift = drawer.open(self.ctx, 10000)
        before = self.db.one("SELECT count(*) FROM audit_log")[0]
        with self.assertRaises(ValidationFailed) as error:
            drawer.close(self.ctx, denominations={"10000": 1}, float_left=2000, shift_id=shift)
        self.assertEqual(error.exception.code, "float_split_impossible")
        self.assertEqual(drawer.summary(self.ctx)["id"], shift)
        self.assertFalse(self.db.one("SELECT 1 FROM money_movements WHERE org_id=?", (self.ctx.org_id,)))
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log")[0], before)
        result = drawer.close(self.ctx, denominations={"5000": 1, "2000": 2, "1000": 1},
                              float_left=2000, shift_id=shift)
        self.assertEqual(result["float_split"], {"keep": {"2000": 1},
                                                 "move": {"5000": 1, "2000": 1, "1000": 1}, "float_left": 2000})
        self.assertEqual(self.db.one("SELECT amount FROM money_movements WHERE ref_id=?", (shift,))[0], 8000)


class TestShiftCloseDefaultFloat(DBTestCase):
    """Review of #500 (P1): with no float typed, the server's own default (the opening cash) was refused when the
    counted notes could not make it, and the close screen said «عدّها بإيدك»: a close nobody could finish without
    guessing a float. The default now leaves the largest amount up to the opening cash that the notes make."""

    def test_untyped_float_leaves_what_the_notes_can_make(self):
        drawer = CashDrawer(self.db)
        shift = drawer.open(self.ctx, 7500)
        result = drawer.close(self.ctx, denominations={"5000": 2}, shift_id=shift, note="فرق تجربة")  # two 50s
        self.assertEqual(result["float_split"]["float_left"], 5000)                    # 75 cannot be made: 50 stays
        self.assertEqual(self.db.one("SELECT amount FROM money_movements WHERE ref_id=?", (shift,))[0], 5000)

    def test_untyped_float_with_no_note_small_enough_moves_everything(self):
        drawer = CashDrawer(self.db)
        shift = drawer.open(self.ctx, 2000)
        result = drawer.close(self.ctx, denominations={"5000": 1}, shift_id=shift, note="فرق تجربة")
        self.assertEqual(result["float_split"]["float_left"], 0)
        self.assertEqual(self.db.one("SELECT amount FROM money_movements WHERE ref_id=?", (shift,))[0], 5000)

    def test_a_typed_float_is_still_refused_when_the_notes_cannot_make_it(self):
        drawer = CashDrawer(self.db)
        shift = drawer.open(self.ctx, 7500)
        with self.assertRaises(ValidationFailed) as error:
            drawer.close(self.ctx, denominations={"5000": 2}, float_left=7500, shift_id=shift, note="فرق تجربة")
        self.assertEqual(error.exception.code, "float_split_impossible")


class TestLegacyVat(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        self.supplier = catalog.create_supplier(self.db, self.ctx, name="Synthetic VAT supplier")
        self.item = catalog.create_item(self.db, self.ctx, {"name_en": "Synthetic VAT item", "public_price": 121})
        self.expiry = (date.today() + timedelta(days=700)).isoformat()

    def check_posting(self, invoice_id):
        draft = self.p.get(self.ctx, invoice_id)
        line = draft["lines"][0]
        self.assertIsNone(line["tax_included"])
        self.assertEqual((line["unit_net_price"], line["line_total"], line["tax_amount"]), (51, 102, 1))
        self.assertEqual((draft["invoice"]["declared_total"], draft["invoice"]["computed_total"]), (122, 122))
        self.assertTrue(draft["can_approve"], draft["issues"])
        CashDrawer(self.db).open(self.ctx, 1000)
        self.p.approve(self.ctx, invoice_id, None)
        ledger = self.db.all("SELECT kind, amount FROM supplier_ledger WHERE org_id=? AND ref_id=? ORDER BY rowid",
                             (self.ctx.org_id, invoice_id))
        self.assertEqual([tuple(row) for row in ledger], [("purchase", 122), ("payment", -122)])
        self.assertEqual(money.drawer_balance(self.db, self.ctx)["balance"], 878)
        result = returns.create_from_invoice(self.db, self.ctx, self.supplier, invoice_id,
                                             [{"purchase_line_id": line["id"], "paid_units": 2, "bonus_units": 0,
                                               "reason": "expired"}])
        self.assertEqual(result["total"], 122)

    def legacy_draft(self):
        invoice_id = self.p.create_manual(self.ctx, supplier_id=self.supplier)
        self.p.update_header(self.ctx, invoice_id, {"invoice_no": "LEGACY-VAT", "declared_total": 122,
                                                   "pay_source": "drawer"}, None)
        self.p.add_line(self.ctx, invoice_id, {"item_id": self.item, "qty": 2, "public_price": 121,
                                              "discount_bp": 5000, "distributor_margin": 10, "tax_amount": 1,
                                              "batch_no": "LV-1", "expiry_date": self.expiry}, None)
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoices SET computed_total=123 WHERE id=?", (invoice_id,))
        return invoice_id

    def test_saved_pre_vat_mode_draft_with_cached_overcharge(self):
        invoice_id = self.legacy_draft()
        self.check_posting(invoice_id)

    def test_last_cost_check_uses_the_same_vat_amount_as_posting(self):
        previous = self.p.create_manual(self.ctx, supplier_id=self.supplier)
        self.p.update_header(self.ctx, previous, {"invoice_no": "PREVIOUS", "declared_total": 50}, None)
        self.p.add_line(self.ctx, previous, {"item_id": self.item, "qty": 1, "unit_net_price": 50,
                                           "batch_no": "LV-0", "expiry_date": self.expiry}, None)
        self.p.approve(self.ctx, previous, None)
        entry_settings.save(self.db, self.ctx, {"last_cost_delta_pct": 22})
        self.check_posting(self.legacy_draft())

    def test_previously_posted_invoice_still_credits_original_charge(self):
        invoice_id = self.legacy_draft()
        line_id = self.p.get(self.ctx, invoice_id)["lines"][0]["id"]
        CashDrawer(self.db).open(self.ctx, 1000)
        original_amount = validation.effective_amount
        with patch.object(validation, "effective_amount", side_effect=lambda line, **_: original_amount(line)):
            self.p.approve(self.ctx, invoice_id, None)
        result = returns.create_from_invoice(self.db, self.ctx, self.supplier, invoice_id,
                                             [{"purchase_line_id": line_id, "paid_units": 2, "bonus_units": 0,
                                               "reason": "expired"}])
        self.assertEqual(result["total"], 123)

    def test_imported_line_with_vat_and_distributor_margin(self):
        data = ("Item,Qty,Public Price,Discount,VAT,Batch\n"
                "Synthetic VAT item,2,1.21,50%,0.01,LV-2\n").encode()
        invoice_id = self.p.import_file(self.ctx, data, "synthetic-vat.csv", supplier_id=self.supplier)
        line = self.p.get(self.ctx, invoice_id)["lines"][0]
        self.p.update_line(self.ctx, invoice_id, line["id"], {"item_id": self.item, "distributor_margin": 10,
                                                             "expiry_date": self.expiry}, None)
        self.p.update_header(self.ctx, invoice_id, {"invoice_no": "IMPORT-VAT", "declared_total": 122,
                                                   "pay_source": "drawer"}, None)
        self.check_posting(invoice_id)
