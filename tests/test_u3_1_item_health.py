"""U3-1 «فحص بيانات الأصناف»: each check finds its case, nothing is changed, owner/manager only, org-isolated."""
from dataclasses import replace
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Forbidden
from app.inventory import service as inv
from app.reports.item_health import item_health, pack_problem, strip_suggestions
from tests.helpers import DBTestCase


def keys(r, item_id):
    return {x["key"] for x in r["rows"] if x["item_id"] == item_id}


class TestPackRule(DBTestCase):
    def test_ranxapin_real_case(self):
        p = pack_problem("RANXAPIN 10MG 21TAB", 6, "شريط")
        self.assertIsNotNone(p)
        self.assertEqual(p["suggest"], [3])            # 3 strips of 7
        self.assertIn("3 شريط × 7", p["text"])
        self.assertIsNone(pack_problem("RANXAPIN 10MG 21TAB", 3, "شريط"))

    def test_tablet_unit_must_equal_count(self):
        self.assertIsNotNone(pack_problem("CONCOR 5MG 30 TAB", 3, "قرص"))
        self.assertIsNone(pack_problem("CONCOR 5MG 30 TAB", 30, "قرص"))

    def test_no_count_or_whole_box_is_fine(self):
        self.assertIsNone(pack_problem("Old Syrup 100ml", 1, "unit"))
        self.assertIsNone(pack_problem("Panadol 500mg 24 tab", 1, "شريط"))
        self.assertIsNone(pack_problem("Panadol 500mg 24 tab", 2, "شريط"))

    def test_strip_equal_to_count_is_questioned(self):
        self.assertIsNotNone(pack_problem("Augmentin 1gm 14 tab", 14, "شريط"))

    def test_suggestions(self):
        self.assertEqual(strip_suggestions(30), [2, 3, 5, 6])
        self.assertEqual(strip_suggestions(21), [3])


class TestItemHealth(DBTestCase):
    def setUp(self):
        super().setUp()
        c = lambda d, **k: cat.create_item(self.db, self.ctx, d, **k)
        self.ranx = c({"name_en": "RANXAPIN 10MG 21TAB", "pack_size": 6, "base_unit_name": "شريط", "public_price": 14550})
        self.ok = c({"name_en": "Panadol 500mg 24 tab", "pack_size": 2, "base_unit_name": "شريط", "public_price": 4000})
        self.odd = c({"name_en": "Odd Price 30 tab", "pack_size": 3, "base_unit_name": "شريط", "public_price": 1000})
        self.zero = c({"name_en": "Zero Price Cream"})
        self.dear = c({"name_en": "Dear Cost Syrup", "public_price": 1000})
        self.neg = c({"name_en": "Negative Gel", "public_price": 500})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.dear, "B1", "2030-01-01")
            inv.post_movement(self.db, self.ctx, item_id=self.dear, qty_delta=2, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("12"))      # 1200 > 1000
            inv.post_movement(self.db, self.ctx, item_id=self.zero, qty_delta=3, batch_id=None,
                              movement_type="opening", unit_cost=Decimal("1"))          # no expiry, no price
        with self.db.transaction():       # a negative balance can only come from old imported data
            self.db.execute("INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty) VALUES (?,?,?,'',-2)",
                            (self.ctx.org_id, self.ctx.branch_id, self.neg))

    def test_each_check(self):
        r = item_health(self.db, self.ctx)
        self.assertIn("pack_vs_name", keys(r, self.ranx))
        self.assertEqual(keys(r, self.ok), set())
        self.assertIn("unit_price_split", keys(r, self.odd))     # 10.00 / 3: 3.33 × 3 = 9.99
        self.assertTrue({"no_public_price", "stock_no_expiry"} <= keys(r, self.zero))
        self.assertIn("cost_above_public", keys(r, self.dear))
        self.assertIn("negative_stock", keys(r, self.neg))
        self.assertEqual(r["rows"][0]["status"], "bad")          # worst first

    def test_nothing_changes(self):
        before = self.db.all("SELECT * FROM items ORDER BY id")
        n = self.db.one("SELECT count(*) n FROM stock_movements")["n"]
        item_health(self.db, self.ctx)
        self.assertEqual([dict(x) for x in self.db.all("SELECT * FROM items ORDER BY id")], [dict(x) for x in before])
        self.assertEqual(self.db.one("SELECT count(*) n FROM stock_movements")["n"], n)

    def test_cost_hidden_without_cost_rights(self):
        mgr = replace(self.ctx, permissions=self.ctx.permissions - {"costs.read"})
        r = item_health(self.db, mgr)
        self.assertNotIn("cost_above_public", keys(r, self.dear))
        self.assertFalse(r["costs"])

    def test_owner_or_manager_only(self):
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"items.price"})
        with self.assertRaises(Forbidden):
            item_health(self.db, staff)

    def test_duplicate_names_and_barcode_as_code(self):
        a = cat.create_item(self.db, self.ctx, {"name_en": "Twin Item", "code": "6221000012341", "public_price": 100})
        b = cat.create_item(self.db, self.ctx, {"name_en": "Twin Item", "public_price": 100}, force=True, dedupe_suffix="2",
                            barcodes=["6221000012341"])
        r = item_health(self.db, self.ctx)
        self.assertIn("duplicate_name", keys(r, a))
        self.assertIn("duplicate_name", keys(r, b))
        self.assertIn("barcode_is_other_code", keys(r, b))

    def test_other_pharmacy_not_seen(self):
        _, other = self.second_tenant()
        r = item_health(self.db, other)
        self.assertEqual(r["items_checked"], 0)


from app.catalog import repack                                   # noqa: E402
from app.core.errors import Conflict, ValidationFailed          # noqa: E402
from tests.test_sales import SalesCase                          # noqa: E402


class TestPackCorrection(SalesCase):
    """Panadol in SalesCase: pack 2, OLD lot 3 units, NEW lot 10 units, cost 15/16 per unit."""

    def _fix(self, n, reason="العبوة الحقيقية"):
        return repack.correct_pack(self.db, self.ctx, self.pan, n, reason)

    def test_converts_lots_and_cost_keeps_boxes(self):
        # make both lots whole boxes first (3 units of pack 2 is a box and a half)
        with self.db.transaction():
            old = self.db.one("SELECT id FROM batches WHERE item_id=? AND batch_no='OLD'", (self.pan,))["id"]
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=1, batch_id=old,
                              movement_type="purchase", unit_cost=Decimal("15"))
        cost_before = Decimal(self.db.one("SELECT avg_cost FROM item_costs WHERE item_id=?", (self.pan,))["avg_cost"])
        self._fix(4)                                          # really 4 strips a box
        st = {b["batch_no"]: b["qty"] for b in inv.stock_for_item(self.db, self.ctx, self.pan)["batches"]}
        self.assertEqual(st, {"OLD": 8, "NEW": 20})          # 2 + 5 boxes stay 2 + 5 boxes
        cost_after = Decimal(self.db.one("SELECT avg_cost FROM item_costs WHERE item_id=?", (self.pan,))["avg_cost"])
        self.assertEqual((cost_after * 4).quantize(Decimal("0.0001")), (cost_before * 2).quantize(Decimal("0.0001")))
        self.assertEqual(self.db.one("SELECT pack_size FROM items WHERE id=?", (self.pan,))["pack_size"], 4)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='item.pack_corrected' AND entity_id=?", (self.pan,)))
        mv = self.db.all("SELECT ref_type, movement_type FROM stock_movements WHERE ref_type='pack_fix'")
        self.assertEqual({(m["ref_type"], m["movement_type"]) for m in mv}, {("pack_fix", "adjustment")})

    def test_open_box_refused(self):
        with self.assertRaises(Conflict) as e:
            self._fix(3)                                      # OLD lot = 3 units of 2 = 1.5 boxes -> 2.25 units
        self.assertEqual(e.exception.code, "pack_not_whole")
        self.assertEqual(self.db.one("SELECT pack_size FROM items WHERE id=?", (self.pan,))["pack_size"], 2)

    def test_reason_and_same_pack(self):
        with self.assertRaises(ValidationFailed):
            self._fix(4, reason="")
        with self.assertRaises(ValidationFailed):
            self._fix(2)

    def test_refused_while_on_a_draft_sale(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        with self.assertRaises(Conflict) as e:
            self._fix(1)
        self.assertEqual(e.exception.code, "pack_in_draft")

    def test_return_of_older_sale_refused_after_fix(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        # left: OLD 1, NEW 10 -> pack 2 -> 4 needs whole conversion: 1*4/2 = 2 ok
        self._fix(4)
        with self.assertRaises(Conflict) as e:
            self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        self.assertEqual(e.exception.code, "pack_corrected_after")
        with self.assertRaises(Conflict):
            self.s.void(self.ctx, sid, "غلط في الفاتورة")
        # a sale made after the fix returns normally
        sid2 = self.s.create(self.ctx)
        d2 = self.s.add_line(self.ctx, sid2, self.pan, units=4)
        self.s.complete(self.ctx, sid2, paid_cash=d2["total"], safety_decisions=self.ack_all(sid2))
        self.s.return_items(self.ctx, sid2, {d2["lines"][0]["id"]: 4}, refund_method="cash", reason="test")
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def _whole_boxes(self):
        with self.db.transaction():
            old = self.db.one("SELECT id FROM batches WHERE item_id=? AND batch_no='OLD'", (self.pan,))["id"]
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=1, batch_id=old,
                              movement_type="purchase", unit_cost=Decimal("15"))
        return old

    def test_supplier_return_of_an_old_lot_refused_new_lot_fine(self):
        """Review P1: a lot received before the fix counts its receipts in old units."""
        from app.procurement import returns
        old = self._whole_boxes()
        sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self._fix(4)
        with self.assertRaises(Conflict) as e:
            returns.create(self.db, self.ctx, sup, [{"item_id": self.pan, "batch_id": old, "qty_base": 4, "reason": "expired"}])
        self.assertEqual(e.exception.code, "pack_corrected_after")
        with self.db.transaction():
            fresh = inv.get_or_create_batch(self.db, self.ctx, self.pan, "AFTER", "2031-01-01")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=8, batch_id=fresh,
                              movement_type="purchase", unit_cost=Decimal("8"))
        returns.create(self.db, self.ctx, sup, [{"item_id": self.pan, "batch_id": fresh, "qty_base": 4, "reason": "expired"}])

    def test_loss_report_ignores_the_fix(self):
        from app.inventory.adjust import loss_report
        from tests.helpers import today
        self._whole_boxes()
        self._fix(4)
        r = loss_report(self.db, self.ctx, today(), today())
        self.assertEqual((r["rows"], r["total"]), ([], 0))

    def test_needs_adjust_right(self):
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"inventory.adjust"})
        with self.assertRaises(Forbidden):
            repack.correct_pack(self.db, staff, self.pan, 4, "سبب")

    def test_open_credit_counted(self):
        from app.db import new_id
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,?)",
                            (cid, self.ctx.org_id, "عميل تجربة", 1000000))
        sid = self.s.create(self.ctx, cid)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual(repack.preview(self.db, self.ctx, self.pan, 4)["open_credit"], 1)
        self.assertEqual(repack.preview(self.db, self.ctx, self.pan, 4)["open_credit"], 1)


from tests.test_owner_plan_priority_1_2 import PlanCase          # noqa: E402


class TestItemHealthAPI(PlanCase):
    def test_report_and_pack_fix_through_the_screen(self):
        r = self.call("GET", "/api/reports/item-health")
        self.assertIn("rows", r)
        p = self.call("GET", f"/api/items/{self.box}/pack-fix?pack=6")
        self.assertEqual([l["new_qty"] for l in p["lots"]], [60])
        self.call("GET", f"/api/items/{self.box}/pack-fix?pack=x", 422)
        self.call("POST", f"/api/items/{self.box}/pack-fix", 422, json={"pack_size": 6, "reason": ""})
        self.call("POST", f"/api/items/{self.box}/pack-fix", json={"pack_size": 6, "reason": "العبوة غلط"})
        self.assertEqual(self.qty(self.box), 60)
        self.call("POST", f"/api/items/{self.box}/pack-fix", 422, json={"pack_size": 6, "reason": "تاني"})   # retry: no double


class TestCodexOn420(TestItemHealth):
    def test_hidden_items_need_the_right(self):
        with self.db.transaction():
            self.db.execute("UPDATE items SET hidden=1 WHERE id=?", (self.ranx,))
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"items.hidden"})
        self.assertNotIn(self.ranx, {r["item_id"] for r in item_health(self.db, staff)["rows"]})
        self.assertIn(self.ranx, {r["item_id"] for r in item_health(self.db, self.ctx)["rows"]})


class TestCodexOn420Order(TestPackCorrection):
    def test_old_lot_found_by_ledger_position_not_timestamp(self):
        from app.procurement import returns
        old = self._whole_boxes()
        sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self._fix(4)
        from unittest import mock
        # a correction stamp that sorts before the receipts as text (mixed precision): the ledger position decides
        with mock.patch.object(repack, "corrected_at", return_value="0000-01-01T00:00:00.000000Z"):
            with self.assertRaises(Conflict):
                returns.create(self.db, self.ctx, sup, [{"item_id": self.pan, "batch_id": old, "qty_base": 4, "reason": "expired"}])
