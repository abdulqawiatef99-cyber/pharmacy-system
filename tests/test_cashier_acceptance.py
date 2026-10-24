"""Cashier acceptance matrix (automated part). Hardware and Windows checks are
manual: docs/WINDOWS_ACCEPTANCE.md."""
from datetime import date, timedelta
from decimal import Decimal

from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

T = date.today()


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s, self.cd = Sales(self.db), CashDrawer(self.db)
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 4000}, barcodes=["6221025012345"])
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (T + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=20, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("15"))

    def sell(self, ctx=None, units=2, cash=None, card=0, credit=0, customer=None):
        ctx = ctx or self.ctx
        sid = self.s.create(ctx, customer)
        d = self.s.add_line(ctx, sid, self.pan, units=units)
        f = self.s.safety_check(ctx, sid)
        return self.s.complete(ctx, sid, paid_cash=d["total"] - card - credit if cash is None else cash,
                               paid_card=card, on_credit=credit, safety_decisions={i: "ok" for i in range(len(f))})


class TestShift(Base):
    def test_cash_sale_requires_open_shift(self):
        with self.assertRaises(Conflict) as e:
            self.sell()
        self.assertEqual(e.exception.code, "no_open_shift")
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 20)   # nothing moved
        self.sell(cash=0, card=4000)            # card-only sale needs no drawer

    def test_drawer_totals_match_every_operation(self):
        self.cd.open(self.ctx, 10000)                               # 100 EGP float
        a = self.sell(units=2)                                      # cash 40
        self.sell(units=2, card=4000)                               # card 40
        cust = self.s.create_customer(self.ctx, name="عميل", credit_limit=100000)
        self.sell(units=2, credit=4000, customer=cust)              # credit 40
        self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")   # -20
        v = self.sell(units=2)                                      # cash 40 ...
        self.s.void(self.ctx, v["id"], "خطأ في الفاتورة")             # ... then voided: -40
        self.s.customer_payment(self.ctx, cust, 1500)               # cash 15
        self.cd.movement(self.ctx, 500, "payout", "مواصلات")          # -5
        sm = self.cd.summary(self.ctx)
        self.assertEqual(sm["expected_cash"], 10000 + 4000 - 2000 + 4000 - 4000 + 1500 - 500)
        self.assertEqual((sm["card_total"], sm["credit_total"]), (4000, 4000))
        with self.assertRaises(ValidationFailed):                    # difference needs a reason
            self.cd.close(self.ctx, sm["expected_cash"] - 100)
        r = self.cd.close(self.ctx, sm["expected_cash"] - 100, "نقص 1 جنيه فكة")
        self.assertEqual((r["status"], r["difference"]), ("closed", -100))
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_one_open_shift_and_permissions(self):
        self.cd.open(self.ctx, 0)
        with self.assertRaises(Conflict):
            self.cd.open(self.ctx, 0)
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        self.sell(ctx=actx)                                         # assistant can sell
        with self.assertRaises(Forbidden):
            self.cd.close(actx, 0)
        with self.assertRaises(Forbidden):
            self.cd.movement(actx, 100, "payout", "سحب")


class TestSaleRules(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 0)

    def test_quantity_not_available(self):
        sid = self.s.create(self.ctx)
        with self.assertRaises(Conflict):
            self.s.add_line(self.ctx, sid, self.pan, packs=11)       # 22 > 20
        self.assertEqual(self.s.get(self.ctx, sid)["lines"], [])

    def test_totals_exact_with_discount_and_odd_units(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=3, discount_bp=750)    # 3 x 20.00 = 60.00, 7.5% = 4.50
        d = self.s.get(self.ctx, sid)
        self.assertEqual((d["subtotal"], d["discount_total"], d["total"]), (6000, 450, 5550))
        f = self.s.safety_check(self.ctx, sid)
        done = self.s.complete(self.ctx, sid, paid_cash=5550, safety_decisions={i: "ok" for i in range(len(f))})
        self.assertEqual(done["lines"][0]["line_total"], 5550)

    def test_audit_trail_for_sale_lifecycle(self):
        a = self.sell()
        self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")
        acts = [r[0] for r in self.db.all("SELECT action FROM audit_log WHERE entity_id=? ORDER BY seq", (a["id"],))]
        self.assertEqual(acts, ["sale.complete", "sale.return"])
        from app.core import audit
        self.assertEqual(audit.verify_chain(self.db), [])


class TestBarcodeFlowAPI(APICase):
    def test_scan_barcode_to_receipt(self):
        cat.add_barcode(self.db, self.ctx, self.pan, "6221025012345")
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, movement_type="opening",
                              unit_cost=Decimal("15"))
        self.http_login()
        self.c.post("/api/cash/open", headers=H, json={"opening_cash": 0})
        found = self.c.get("/api/items?q=6221025012345").json()        # what a scanner types
        self.assertEqual([x["id"] for x in found], [self.pan])
        s = self.c.post("/api/sales", headers=H, json={}).json()
        hk = {**H, "Idempotency-Key": "line-1"}
        for _ in range(2):                                             # scanner double-fires
            s = self.c.post(f"/api/sales/{s['id']}/lines", headers=hk,
                            json={"item_id": self.pan, "packs": 1, "client_ref": "scan-1"}).json()
        self.assertEqual(len(s["lines"]), 1)
        body = {"paid_cash": s["total"], "version": s["version"], "request_id": "pay-1"}
        r1 = self.c.post(f"/api/sales/{s['id']}/complete", headers=H, json=body)
        r2 = self.c.post(f"/api/sales/{s['id']}/complete", headers=H, json=body)   # retry after timeout
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r1.json()["sale_no"], r2.json()["sale_no"])
        self.assertEqual(self.c.get("/api/cash/current").json()["expected_cash"], 4000)
        self.assertEqual(self.c.get(f"/receipt/{s['id']}").status_code, 200)
