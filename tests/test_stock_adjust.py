"""Stock changes with a reason, supplier returns, loss report."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import notify, people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.inventory import adjust, service as inv
from app.inventory.counting import Counting
from app.procurement import returns, suppliers as sup
from app.sales.service import local_today
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class TestAdjust(Base):
    def setUp(self):
        super().setUp()
        with self.db.transaction():
            self.b2 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B2", (T + timedelta(days=30)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=6, batch_id=self.b2, movement_type="purchase",
                              unit_cost=Decimal("15"))
        self.today = local_today().isoformat()

    def test_reasons_values_and_report(self):
        with self.assertRaises(ValidationFailed):
            adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-1, reason="found")           # "found" only adds
        with self.assertRaises(ValidationFailed):
            adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-1, reason="other")           # "other" needs a note
        r = adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-2, batch_id=self.b2, reason="damaged")
        self.assertEqual((r["before"], r["after"], r["value"]), (6, 4, -3000))                  # 2 x 15 EGP
        m = self.db.one("SELECT movement_type, reason FROM stock_movements WHERE batch_id=? ORDER BY rowid DESC", (self.b2,))
        self.assertEqual((m["movement_type"], m["reason"]), ("damage", "damaged"))
        adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-3, reason="lost")                 # FEFO: the nearer batch first
        self.assertEqual(inv.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, self.pan, self.b2), 1)
        adjust.adjust(self.db, self.ctx, self.pan, set_to=0, reason="expired")                  # zero the item
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 0)
        rep = adjust.loss_report(self.db, self.ctx, self.today, self.today)
        g = {x["reason"]: x for x in rep["groups"]}
        self.assertEqual((g["damaged"]["qty_out"], g["lost"]["qty_out"], g["expired"]["qty_out"]), (2, 3, 21))
        self.assertEqual(rep["total"], -(26 * 1500))
        self.assertIn("stock_loss", [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]])

    def test_permission_and_too_much(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        with self.assertRaises(Forbidden):
            adjust.adjust(self.db, self.login("asst", PW), self.pan, qty_delta=-1, reason="lost")
        with self.assertRaises(Conflict):
            adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-999, reason="lost")

    def test_count_with_reasons(self):
        c = Counting(self.db)
        sid = c.create(self.ctx, title="رف A", scope="items", item_ids=[self.pan])
        c.record(self.ctx, sid, item_id=self.pan, qty=4, batch_id=self.b2)
        c.approve(self.ctx, sid, reasons={f"{self.pan}|{self.b2}": "expired"})
        m = self.db.one("SELECT movement_type, reason, qty_delta FROM stock_movements WHERE ref_id=?", (sid,))
        self.assertEqual((m["movement_type"], m["reason"], m["qty_delta"]), ("damage", "expired", -2))
        with self.assertRaises(ValidationFailed):
            sid2 = c.create(self.ctx, title="x", scope="items", item_ids=[self.pan])
            c.approve(self.ctx, sid2, shortage_reason="nope")


class TestSupplierReturn(Base):
    def test_return_lowers_stock_and_supplier_balance(self):
        sid = cat.create_supplier(self.db, self.ctx, name="مخزن الشفاء")
        b1 = self.db.one("SELECT id FROM batches WHERE item_id=?", (self.pan,))["id"]
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.pan, b1), 1500)
        with self.assertRaises(ValidationFailed):
            returns.create(self.db, self.ctx, sid, [{"item_id": self.pan, "batch_id": b1, "qty_base": 2}])   # no reason
        r = returns.create(self.db, self.ctx, sid, [{"item_id": self.pan, "batch_id": b1, "qty_base": 4, "reason": "near_expiry"}], "مندوب أحمد")
        self.assertEqual((r["return_no"], r["total"]), (1, 6000))
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 16)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, sid), -6000)                       # supplier owes us now
        with self.assertRaises(Conflict):
            returns.create(self.db, self.ctx, sid, [{"item_id": self.pan, "batch_id": b1, "qty_base": 99, "reason": "expired"}])
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, sid), -6000)                       # failed one left nothing
        t = local_today().isoformat()
        self.assertEqual(returns.listing(self.db, self.ctx, t, t)["total"], 6000)
        self.assertEqual(adjust.loss_report(self.db, self.ctx, t, t)["returned_to_suppliers"], -6000)


class TestReturnSlipAPI(APICase):
    def test_slip_and_adjust_api(self):
        self.http_login()
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "Z1", "2030-01-31")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("12"))
        sid = self.db.one("SELECT id FROM suppliers")["id"]
        r = self.c.post("/api/supplier-returns", json={"supplier_id": sid, "lines": [{"item_id": self.pan, "batch_id": b,
                        "qty_base": 2, "reason": "damaged"}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        page = self.c.get(f"/supplier-return/{r.json()['id']}")
        self.assertIn("إذن مرتجع للمورد رقم 1", page.text)
        a = self.c.post(f"/api/items/{self.pan}/adjust", json={"qty_delta": -1, "reason": "lost"}, headers=H)
        self.assertEqual(a.json()["after"], 7)
        self.assertIn("expired", self.c.get("/api/stock/reasons").json()["adjust"])
