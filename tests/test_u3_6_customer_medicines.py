"""U3-6 «أدوية العميل»: a customer's items of the last 6 months, most bought first, his last quantity, stock and
refill date; returned items left out; only his pharmacy."""
from app.core.errors import NotFound
from app.db import new_id
from app.sales import customers as cu
from tests.test_sales import SalesCase


class TestUsualItems(SalesCase):
    def setUp(self):
        super().setUp()
        self.cust = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,?)",
                            (self.cust, self.ctx.org_id, "عميل تجربة", 0))

    def sell(self, units):
        sid = self.s.create(self.ctx, self.cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        return self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))

    def test_items_last_qty_and_refill(self):
        self.sell(1)
        self.sell(2)
        cu.save_refill(self.db, self.ctx, self.cust, self.pan, 2, 30)
        it = cu.usual_items(self.db, self.ctx, self.cust)
        self.assertEqual([(x["item_id"], x["times"], x["last_qty"]) for x in it], [(self.pan, 2, 2)])
        self.assertIsNotNone(it[0]["refill_due"])
        self.assertTrue(it[0]["in_stock"])

    def test_fully_returned_and_old_left_out(self):
        d = self.sell(1)
        self.s.return_items(self.ctx, d["id"], {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        self.assertEqual(cu.usual_items(self.db, self.ctx, self.cust), [])
        self.sell(1)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at='2020-01-01T10:00:00.000000Z' WHERE customer_id=?", (self.cust,))
        self.assertEqual(cu.usual_items(self.db, self.ctx, self.cust), [])

    def test_other_pharmacy(self):
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            cu.usual_items(self.db, other, self.cust)


class TestCodexOn419(TestUsualItems):
    def test_hidden_and_expired(self):
        from dataclasses import replace
        self.sell(1)
        with self.db.transaction():
            self.db.execute("UPDATE items SET hidden=1 WHERE id=?", (self.pan,))
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"items.hidden"})
        self.assertEqual(cu.usual_items(self.db, staff, self.cust), [])
        self.assertEqual(len(cu.usual_items(self.db, self.ctx, self.cust)), 1)


class TestCodexOn419Stock(TestUsualItems):
    def test_unsellable_stock_is_not_available(self):
        self.sell(1)
        with self.db.transaction():          # what is left is only expired: nothing to sell
            self.db.execute("UPDATE batches SET expiry_date='2000-01-01' WHERE item_id=?", (self.pan,))
        it = cu.usual_items(self.db, self.ctx, self.cust)[0]
        self.assertEqual((it["stock"], it["in_stock"]), (0, False))

    def test_usual_qty_is_what_he_kept(self):
        d = self.sell(5)
        self.s.return_items(self.ctx, d["id"], {d["lines"][0]["id"]: 4}, refund_method="cash", reason="test")
        self.assertEqual(cu.usual_items(self.db, self.ctx, self.cust)[0]["last_qty"], 1)


class TestCodexOn419Lines(TestUsualItems):
    def test_two_lines_of_the_same_item(self):
        sid = self.s.create(self.ctx, self.cust)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        d = self.s.add_line(self.ctx, sid, self.pan, units=3, discount_bp=500)    # kept on its own line
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual(cu.usual_items(self.db, self.ctx, self.cust)[0]["last_qty"], 5)
