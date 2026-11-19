"""Codex audit M2-19 (19/11), the same class in the stock and supplier services: Python counts `True` as the
integer 1, so a service that only asks "is it an int" takes `true` as one piastre or one unit. The HTTP routes
already refuse it (`whole_number`); the services refuse it too, so no other caller can post it.
Synthetic data only."""
from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.inventory import adjust, service as inv
from app.procurement import suppliers as sup
from tests.test_counting import CountCase


class TrueIsNotOne(CountCase):
    def movements(self):
        return self.db.one("SELECT count(*) n FROM stock_movements WHERE org_id=?", (self.ctx.org_id,))["n"]

    def test_a_supplier_payment_of_true_is_refused(self):
        sid = cat.create_supplier(self.db, self.ctx, name="مخزن تجريبي")
        for amount in (True, False, 10.0, "100"):
            with self.assertRaises(ValidationFailed):
                sup.pay(self.db, self.ctx, sid, amount, "cash", "دفعة")
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, sid), 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_ledger WHERE org_id=?", (self.ctx.org_id,))["n"], 0)

    def test_a_counted_quantity_of_true_is_refused(self):
        sid = self.cnt.create(self.ctx, title="جرد تجريبي")
        with self.assertRaises(ValidationFailed):
            self.cnt.record(self.actx, sid, item_id=self.b, qty=True)
        self.assertEqual(self.db.one("SELECT count(*) n FROM count_entries")["n"], 0)

    def test_a_stock_movement_of_true_is_refused(self):
        before = self.movements()
        with self.db.transaction():
            with self.assertRaises(ValidationFailed):
                inv.post_movement(self.db, self.ctx, item_id=self.b, qty_delta=True, batch_id=None,
                                  movement_type="adjustment")
        self.assertEqual(self.movements(), before)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.b)["total_qty"], 5)

    def test_an_adjustment_of_true_is_refused(self):
        before = self.movements()
        with self.assertRaises(ValidationFailed):
            adjust.adjust(self.db, self.ctx, self.b, qty_delta=True, reason="found", note="جرد")
        self.assertEqual(self.movements(), before)
