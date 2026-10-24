from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.inventory import service as inv
from app.safety.engine import SafetyEngine
from app.sales.service import Sales, unit_price
from tests.helpers import DBTestCase

TODAY = date.today()


class SalesCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s = Sales(self.db)
        self.se = SafetyEngine(self.db)
        # Panadol: pack of 2 strips. Old batch priced 38 EGP, new batch 40 EGP (price rise).
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 4000})
        self.exp_item = cat.create_item(self.db, self.ctx, {"name_en": "Old Syrup 100ml", "public_price": 2500})
        with self.db.transaction():
            old = inv.get_or_create_batch(self.db, self.ctx, self.pan, "OLD", (TODAY + timedelta(days=200)).isoformat())
            new = inv.get_or_create_batch(self.db, self.ctx, self.pan, "NEW", (TODAY + timedelta(days=600)).isoformat())
            self.db.execute("UPDATE batches SET public_price=3800 WHERE id=?", (old,))
            self.db.execute("UPDATE batches SET public_price=4000 WHERE id=?", (new,))
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=3, batch_id=old,
                              movement_type="purchase", unit_cost=Decimal("15"))
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=new,
                              movement_type="purchase", unit_cost=Decimal("16"))
            expired = inv.get_or_create_batch(self.db, self.ctx, self.exp_item, "EXP",
                                              (TODAY - timedelta(days=5)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.exp_item, qty_delta=4, batch_id=expired,
                              movement_type="purchase", unit_cost=Decimal("10"))
        # verified composition so the only remaining notice is "interactions not checked"
        self.se.set_item_ingredients(self.ctx, self.pan, [("Paracetamol", "500mg")])
        self.se.verify_item_ingredients(self.ctx, self.pan)

    def ack_all(self, sid):
        f = self.s.safety_check(self.ctx, sid)
        return {i: "تمت المراجعة" for i in range(len(f))}


class TestSales(SalesCase):
    def test_fefo_with_legal_batch_prices(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=5)   # 3 from OLD @19, 2 from NEW @20
        l = d["lines"][0]
        self.assertEqual([(a["batch_no"], a["qty"], a["unit_price"]) for a in l["allocations"]],
                         [("OLD", 3, 1900), ("NEW", 2, 2000)])
        self.assertEqual(d["total"], 3 * 1900 + 2 * 2000)
        d = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual((d["status"], d["sale_no"]), ("completed", 1))
        st = inv.stock_for_item(self.db, self.ctx, self.pan)
        self.assertEqual({b["batch_no"]: b["qty"] for b in st["batches"]}, {"NEW": 8})
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])
        sid2 = self.s.create(self.ctx)
        d2 = self.s.add_line(self.ctx, sid2, self.pan, packs=1)
        d2 = self.s.complete(self.ctx, sid2, paid_card=d2["total"], safety_decisions=self.ack_all(sid2))
        self.assertEqual(d2["sale_no"], 2)                        # sequential numbering

    def test_never_sells_expired(self):
        sid = self.s.create(self.ctx)
        with self.assertRaises(Conflict) as e:
            self.s.add_line(self.ctx, sid, self.exp_item, units=1)
        self.assertEqual(e.exception.details["expired"], 4)

    def test_cannot_oversell(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=10)
        with self.assertRaises(Conflict):
            self.s.add_line(self.ctx, sid, self.pan, units=4)      # 13 available in total

    def test_safety_gate_blocks_until_resolved(self):
        # second product with the same active ingredient -> duplicate therapy alert (high)
        other = cat.create_item(self.db, self.ctx, {"name_en": "Adol 500mg 20 tab", "public_price": 2000})
        self.se.set_item_ingredients(self.ctx, other, [("Paracetamol", "500mg")])
        self.se.verify_item_ingredients(self.ctx, other)
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=other, qty_delta=5, movement_type="opening")
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=1)
        d = self.s.add_line(self.ctx, sid, other, units=1)
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, paid_cash=d["total"])
        self.assertEqual(e.exception.code, "safety_unresolved")
        codes = [f["code"] for f in e.exception.details["findings"]]
        self.assertIn("duplicate_ingredient", codes)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, other)["total_qty"], 5)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        ev = self.db.one("SELECT decision, reason FROM safety_events WHERE finding_code='duplicate_ingredient'"
                         " AND decision='overridden'")
        self.assertEqual(ev["reason"], "تمت المراجعة")

    def test_unmapped_item_is_shown_but_not_a_click_gate(self):
        unmapped = cat.create_item(self.db, self.ctx, {"name_en": "Mystery Tab 10mg", "public_price": 1000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=unmapped, qty_delta=5, movement_type="opening")
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, unmapped, units=1)
        f = self.s.safety_check(self.ctx, sid)
        self.assertIn("insufficient_data", [x.code for x in f])
        self.s.complete(self.ctx, sid, paid_cash=d["total"])

    def test_payment_must_match_and_credit_rules(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, packs=1)
        with self.assertRaises(ValidationFailed):
            self.s.complete(self.ctx, sid, paid_cash=d["total"] - 1, safety_decisions=self.ack_all(sid))
        with self.assertRaises(ValidationFailed):            # credit without customer
            self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        cust = self.s.create_customer(self.ctx, name="أ. محمد", phone="0100", credit_limit=5000)
        sid = self.s.create(self.ctx, customer_id=cust)
        d = self.s.add_line(self.ctx, sid, self.pan, packs=2)  # ~78 EGP > 50 limit
        with self.assertRaises(Conflict) as e:
            self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual(e.exception.code, "credit_limit")
        d = self.s.complete(self.ctx, sid, paid_cash=d["total"] - 3000, on_credit=3000,
                            safety_decisions=self.ack_all(sid))
        self.assertEqual(self.s.balance(self.ctx, cust), 3000)
        self.assertEqual(self.s.customer_payment(self.ctx, cust, 1000), 2000)

    def test_discount_requires_permission(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        sid = self.s.create(actx)
        with self.assertRaises(Forbidden):
            self.s.add_line(actx, sid, self.pan, packs=1, discount_bp=1000)
        d = self.s.add_line(self.ctx, self.s.create(self.ctx), self.pan, packs=1, discount_bp=1000)
        self.assertEqual(d["lines"][0]["discount"], round(d["lines"][0]["gross"] * 0.1))

    def test_partial_return_back_to_same_batch(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=5)
        d = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        line = d["lines"][0]["id"]
        r = self.s.return_items(self.ctx, sid, {line: 2}, refund_method="cash", reason="العميل غير رأيه")
        self.assertEqual(r["refund"], 2 * 2000)                  # last taken (NEW @20) returned first
        st = inv.stock_for_item(self.db, self.ctx, self.pan)
        self.assertEqual({b["batch_no"]: b["qty"] for b in st["batches"]}, {"NEW": 10})
        with self.assertRaises(Conflict):
            self.s.return_items(self.ctx, sid, {line: 4}, refund_method="cash", reason="كمية زيادة")
        with self.assertRaises(Conflict):                        # returned sale can't be voided
            self.s.void(self.ctx, sid, "إلغاء تجريبي")

    def test_void_same_day_restores_stock_and_credit(self):
        cust = self.s.create_customer(self.ctx, name="عميل", credit_limit=100000)
        sid = self.s.create(self.ctx, customer_id=cust)
        d = self.s.add_line(self.ctx, sid, self.pan, packs=1)
        self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        self.s.void(self.ctx, sid, "خطأ في الصنف")
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 13)
        self.assertEqual(self.s.balance(self.ctx, cust), 0)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_day_summary_profit(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=3)     # OLD batch, cost avg
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        rep = self.s.day_summary(self.ctx)
        self.assertEqual((rep["n"], rep["total"], rep["cash"]), (1, 5700, 5700))
        self.assertEqual(rep["unknown_cost_lines"], 0)
        # avg cost after both purchases = (3*15 + 10*16)/13 = 15.769231
        self.assertEqual(rep["cost_egp"], str((Decimal("15.769231") * 3).quantize(Decimal("0.01"))))

    def test_unit_price_rounding(self):
        self.assertEqual(unit_price(4000, 3), 1333)
        self.assertEqual(unit_price(5000, 3), 1667)

    def test_completed_sale_immutable_and_isolated(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, packs=1)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        with self.assertRaises(Conflict):
            self.s.add_line(self.ctx, sid, self.pan, units=1)
        _, other = self.second_tenant()
        from app.core.errors import NotFound
        with self.assertRaises(NotFound):
            self.s.get(other, sid)


class TestConcurrency(SalesCase):
    def test_two_cashiers_last_units(self):
        s1, s2 = self.s.create(self.ctx), self.s.create(self.ctx)
        d1 = self.s.add_line(self.ctx, s1, self.pan, units=13)
        d2 = self.s.add_line(self.ctx, s2, self.pan, units=13)     # both drafts see 13 available
        self.s.complete(self.ctx, s1, paid_cash=d1["total"], safety_decisions=self.ack_all(s1))
        with self.assertRaises(Conflict):
            self.s.complete(self.ctx, s2, paid_cash=d2["total"], safety_decisions=self.ack_all(s2))
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 0)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])
