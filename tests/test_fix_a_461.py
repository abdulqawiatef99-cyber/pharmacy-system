"""Synthetic regressions for the stock and supplier credit findings in #461."""
from datetime import timedelta
from decimal import Decimal

from app.core.errors import Conflict
from app.inventory import service as inv
from app.inventory.counting import Counting
from app.procurement import returns
from app.recalls.service import Recalls
from app.sales.service import Sales, local_today
from tests.test_api import APICase, H
from tests.test_review_324_group_d import _Base


class TestStocktake(APICase):
    def stock(self, item_id, qty, kind="purchase", batch_id=None):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=item_id, qty_delta=qty,
                              movement_type=kind, batch_id=batch_id)

    def check_held_lot(self, recalled):
        counts = Counting(self.db)
        expiry = local_today() + timedelta(days=30 if recalled else -1)
        with self.db.transaction():
            batch = inv.get_or_create_batch(self.db, self.ctx, self.pan, "QA461", expiry.isoformat())
        self.stock(self.pan, 1, batch_id=batch)
        if recalled:
            recalls = Recalls(self.db)
            recalls.add_notice(self.ctx, title="سحب تجريبي",
                               items=[{"product_name": "Panadol 500mg", "batch_numbers": ["QA461"]}])
            recalls.decide(self.ctx, recalls.alerts(self.ctx)[0]["id"], "confirmed")

        sid = counts.create(self.ctx, title="Synthetic count")
        counts.record(self.ctx, sid, item_id=self.pan, qty=2)
        with self.assertRaises(Conflict) as refusal:
            counts.approve(self.ctx, sid)
        self.assertIn("التشغيلة", refusal.exception.message)
        self.assertEqual(counts.variances(self.ctx, sid)["session"]["status"], "open")
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements WHERE ref_id=?", (sid,)))

        counts.cancel(self.ctx, sid)
        sid = counts.create(self.ctx, title="Synthetic lot count")
        counts.record(self.ctx, sid, item_id=self.pan, qty=2, batch_id=batch)
        counts.approve(self.ctx, sid)
        self.assertEqual(inv.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, self.pan, batch), 2)
        sales = Sales(self.db)
        with self.assertRaises(Conflict) as refusal:
            sales.add_line(self.ctx, sales.create(self.ctx), self.pan, units=1)
        self.assertEqual(refusal.exception.details["available"], 0)
        self.assertEqual(refusal.exception.details["recalled" if recalled else "expired"], 2)

    def test_unidentified_surplus_cannot_be_sold_when_the_lot_expired(self):
        self.check_held_lot(recalled=False)

    def test_unidentified_surplus_cannot_be_sold_when_the_lot_is_recalled(self):
        self.check_held_lot(recalled=True)

    def test_same_item_received_after_preview_requires_a_fresh_confirmation(self):
        self.http_login()
        self.stock(self.pan, 36, "opening")
        counts = Counting(self.db)
        sid = counts.create(self.ctx, title="Synthetic count")
        url = f"/api/counts/{sid}"

        def preview():
            response = self.c.get(f"{url}/variances")
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        def approve(view):
            return self.c.post(f"{url}/approve", headers=H, json={
                "zero_uncounted": True, "reason": "Synthetic stocktake confirmation",
                "uncounted_item_ids": [row["item_id"] for row in view["uncounted_with_stock"]],
            })

        old = preview()
        self.assertEqual(old["uncounted_with_stock"][0]["system_qty"], 36)
        self.stock(self.pan, 4)
        fresh = preview()
        self.assertEqual(fresh["uncounted_with_stock"][0]["system_qty"], 40)

        refused = approve(old)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["error"]["code"], "uncounted_changed")
        self.assertEqual(preview()["session"]["status"], "open")
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 40)
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements WHERE ref_id=?", (sid,)))

        approved = approve(fresh)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 0)


class TestSupplierLotReturn(_Base):
    def setUp(self):
        super().setUp()
        self.product = self.item("Synthetic QA461 tab", price=3100)
        self.vendor = self.supplier("Synthetic QA461 supplier")
        self.first, _ = self.buy(self.vendor, self.product, 1, 2000)
        self.second, _ = self.buy(self.vendor, self.product, 1, 2500)
        self.batch = self.lot(self.product)

    def lot_return(self, qty, value=None):
        return returns.create(self.db, self.ctx, self.vendor, [{
            "item_id": self.product, "batch_id": self.batch, "qty_base": qty,
            "reason": "overstock", "unit_value": value,
        }])

    def test_suggested_lot_return_matches_the_charge_for_each_receipt(self):
        shown = returns.suggest_value(self.db, self.ctx, self.product, self.batch, self.vendor)
        self.assertEqual(self.lot_return(2, shown)["total"], 4500)
        self.assertEqual(self.owed(self.vendor), 0)
        self.assertEqual(self.qty(self.product), 0)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_two_partial_lot_returns_credit_each_receipt_once(self):
        self.assertEqual(self.lot_return(1)["total"], 2000)
        self.assertEqual(self.lot_return(1)["total"], 2500)
        self.assertEqual(self.owed(self.vendor), 0)
        self.assertEqual(self.qty(self.product), 0)

    def test_lot_return_then_invoice_return_do_not_credit_the_same_receipt_twice(self):
        self.assertEqual(self.lot_return(1)["total"], 2000)
        first = returns.from_invoice(self.db, self.ctx, self.vendor, "INV-1")["lines"][0]
        second = returns.from_invoice(self.db, self.ctx, self.vendor, "INV-2")["lines"][0]
        self.assertEqual((first["receipt_left"], second["receipt_left"]), (0, 1))
        result = returns.create_from_invoice(self.db, self.ctx, self.vendor, self.second, [{
            "purchase_line_id": second["id"], "paid_units": 1, "reason": "overstock",
        }])
        self.assertEqual(result["total"], 2500)
        self.assertEqual(self.owed(self.vendor), 0)

    def test_manual_credit_above_receipts_is_refused_without_writes(self):
        before = self.owed(self.vendor)
        with self.assertRaises(Conflict) as refusal:
            self.lot_return(2, 3000)
        self.assertIn("4500", refusal.exception.message)
        self.assertEqual(self.owed(self.vendor), before)
        self.assertEqual(self.qty(self.product), 2)
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_returns"))

    def test_lot_and_invoice_returns_preserve_vat_and_zero_value_bonus(self):
        product = self.item("Synthetic QA461 VAT tab", price=5500)
        invoice_id = self.p.create_manual(self.ctx, supplier_id=self.vendor)
        version = self.p.get(self.ctx, invoice_id)["invoice"]["version"]
        version = self.p.update_header(self.ctx, invoice_id, {
            "invoice_no": "VAT-461", "invoice_date": local_today().isoformat(),
        }, version)["invoice"]["version"]
        version = self.p.add_line(self.ctx, invoice_id, {
            "item_id": product, "qty": 2, "bonus_qty": 1, "unit_net_price": 3000,
            "tax_amount": 413, "line_total": 6413, "vat_mode": "additive",
            "batch_no": "VAT-461", "expiry_date": (local_today() + timedelta(days=365)).isoformat(),
        }, version)["invoice"]["version"]
        before = self.owed(self.vendor)
        result = self.p.approve(self.ctx, invoice_id, version)
        self.assertEqual(result["invoice"]["status"], "approved", result["issues"])
        self.assertEqual(self.owed(self.vendor) - before, 6413)

        line = returns.from_invoice(self.db, self.ctx, self.vendor, "VAT-461")["lines"][0]
        lot_line = {"item_id": product, "batch_id": line["batch_id"], "qty_base": 1, "reason": "overstock"}
        with self.assertRaises(Conflict) as refusal:
            returns.create(self.db, self.ctx, self.vendor, [lot_line])
        self.assertEqual(refusal.exception.code, "bonus_line_whole")
        first = returns.create_from_invoice(self.db, self.ctx, self.vendor, invoice_id, [{
            "purchase_line_id": line["id"], "paid_units": 1, "reason": "overstock",
        }], partial_bonus_reason="Synthetic approved partial return")
        rest = returns.create(self.db, self.ctx, self.vendor, [{**lot_line, "qty_base": 2}])
        self.assertEqual(first["total"] + rest["total"], 6413)
        self.assertEqual(first["tax_total"] + rest["tax_total"], 413)
        self.assertEqual([l["unit_value"] for l in rest["lines"] if l["is_bonus"]], [0])
        self.assertEqual(self.owed(self.vendor), before)
        self.assertEqual(self.qty(product), 0)

    def test_two_lines_cannot_reuse_the_same_receipt_in_one_request(self):
        other = self.supplier("Synthetic QA461 second supplier")
        self.buy(other, self.product, 2, 2400)
        line = {"item_id": self.product, "batch_id": self.batch, "qty_base": 2, "reason": "overstock"}
        with self.assertRaises(Conflict) as refusal:
            returns.create(self.db, self.ctx, self.vendor, [line, line])
        self.assertEqual(refusal.exception.code, "return_exceeds")
        self.assertEqual(self.owed(self.vendor), 4500)
        self.assertEqual(self.qty(self.product), 4)
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_returns"))

    def test_another_supplier_cannot_get_credit_against_these_receipts(self):
        other = self.supplier("Synthetic supplier without a receipt for this lot")
        with self.assertRaises(Conflict):
            returns.create(self.db, self.ctx, other, [{
                "item_id": self.product, "batch_id": self.batch, "qty_base": 1, "reason": "overstock",
            }])
        self.assertEqual(self.owed(other), 0)
        self.assertEqual(self.qty(self.product), 2)
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_returns"))

    def test_invoice_return_respects_the_remaining_lot_credit_after_a_legacy_overcredit(self):
        from unittest.mock import patch

        # Older versions credited the latest cost and left the purchase line unset.
        with patch.object(returns, "_lot_return_rows", return_value=[(1, 2500, 0, None, 0)]):
            self.lot_return(1)
        line = returns.from_invoice(self.db, self.ctx, self.vendor, "INV-2")["lines"][0]
        result = returns.create_from_invoice(self.db, self.ctx, self.vendor, self.second, [{
            "purchase_line_id": line["id"], "paid_units": 1, "reason": "overstock",
        }])
        self.assertEqual(result["total"], 2000)
        self.assertEqual(self.owed(self.vendor), 0)
        self.assertEqual(self.qty(self.product), 0)

    def test_explicit_zero_is_respected_even_when_the_latest_receipt_is_free(self):
        invoice = self.p.create_manual(self.ctx, supplier_id=self.vendor)
        draft = self.p.add_line(self.ctx, invoice, {
            "item_id": self.product, "qty": 1, "unit_net_price": 2000,
            "batch_no": "L1", "expiry_date": "12/2029",
        }, None)
        draft = self.p.update_header(self.ctx, invoice, {
            "invoice_no": "FREE-461", "invoice_date": local_today().isoformat(),
            "declared_total": 0, "extra_discount": 2000,
        }, draft["invoice"]["version"])
        draft = self.p.acknowledge(self.ctx, invoice, "unusual_last_cost",
                                   "Synthetic fully discounted receipt", draft["lines"][0]["id"],
                                   draft["invoice"]["version"])
        self.p.approve(self.ctx, invoice, draft["invoice"]["version"])
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.product, self.batch, self.vendor), 0)
        self.assertEqual(self.lot_return(1, 0)["total"], 0)
        self.assertEqual(self.owed(self.vendor), 4500)

    def test_legacy_credit_for_extra_units_still_counts_against_the_lot_limit(self):
        from unittest.mock import patch

        other = self.supplier("Synthetic QA461 second supplier")
        self.buy(other, self.product, 2, 2500)
        with patch.object(returns, "_lot_return_rows", return_value=[(3, 2500, 0, None, 0)]):
            self.lot_return(3)
        latest, _ = self.buy(self.vendor, self.product, 1, 2500)
        before = self.owed(self.vendor)
        self.assertEqual(before, -500)
        line = returns.from_invoice(self.db, self.ctx, self.vendor, "INV-4")["lines"][0]
        result = returns.create_from_invoice(self.db, self.ctx, self.vendor, latest, [{
            "purchase_line_id": line["id"], "paid_units": 1, "reason": "overstock",
        }])
        self.assertEqual(result["total"], 0)
        self.assertEqual(self.owed(self.vendor), before)
        self.assertEqual(self.qty(self.product), 1)

    def test_opening_stock_can_leave_at_zero_after_the_invoiced_units_are_returned(self):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.product, qty_delta=2, batch_id=self.batch,
                              movement_type="opening", unit_cost=Decimal("22"))
        self.assertEqual(self.lot_return(2)["total"], 4500)
        self.assertEqual(self.lot_return(2, 0)["total"], 0)
        self.assertEqual(self.qty(self.product), 0)
        self.assertEqual(self.owed(self.vendor), 0)


class TestSupplierReturnAPI(APICase, _Base):
    def test_a_typed_price_equal_to_the_suggestion_still_gets_the_credit_limit(self):
        self.http_login()
        supplier = self.supplier("Synthetic QA461 supplier")
        self.buy(supplier, self.pan, 1, 2000)
        self.buy(supplier, self.pan, 1, 2500)
        batch = self.lot(self.pan)
        response = self.c.post("/api/supplier-returns", headers=H, json={
            "supplier_id": supplier, "lines": [{
                "item_id": self.pan, "batch_id": batch, "qty_base": 4,
                "reason": "overstock", "unit_value": 1250,
            }],
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "return_value_exceeds")
        self.assertEqual(self.owed(supplier), 4500)
        self.assertEqual(self.qty(self.pan), 4)
