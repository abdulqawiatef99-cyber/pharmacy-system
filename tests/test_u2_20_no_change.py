"""U2-20 «مفيش فكة»: small in-stock items whose price fits in the change (1 to 10 EGP), nearest expiry first,
then margin, then fast sellers; never fridge, prescription, service or out-of-stock items; cost never returned;
a hint when the last close was counted with no one-pound coins."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from app.sales import change_fill
from tests.test_cashier_acceptance import Base, T


class TestNoChange(Base):
    def item(self, name, price, days=300, cost="1", qty=5, **extra):
        i = cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": 1, "public_price": price, **extra})
        if qty:
            with self.db.transaction():
                b = inv.get_or_create_batch(self.db, self.ctx, i, "L" + name, (T + timedelta(days=days)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=i, qty_delta=qty, batch_id=b,
                                  movement_type="purchase", unit_cost=Decimal(cost))
        return i

    def names(self, change):
        return [x["name_en"] for x in change_fill.suggestions(self.db, self.ctx, change)["items"]]

    def test_fits_the_change_and_ranks(self):
        self.item("Gum far", 300, days=400)
        self.item("Gum near", 300, days=40)
        self.item("Tissue high margin", 500, days=400, cost="1")
        self.item("Tissue low margin", 500, days=400, cost="4.5")
        self.item("Too dear", 1200)
        self.item("Too cheap", 50)
        self.item("Rx", 300, rx_required=1)
        self.item("Empty", 300, qty=0)
        got = self.names(600)
        self.assertEqual(got[0], "Gum near")                       # nearest expiry first
        self.assertLess(got.index("Tissue high margin"), got.index("Tissue low margin"))
        for bad in ("Too dear", "Too cheap", "Rx", "Empty"):
            self.assertNotIn(bad, got)
        self.assertNotIn("Tissue high margin", self.names(400))   # above the change
        self.assertEqual(self.names(50), [])                       # below 1 EGP: nothing fits
        r = change_fill.suggestions(self.db, self.ctx, 600)
        self.assertNotIn("cost", str(r).lower())
        self.assertLessEqual(len(r["items"]), 5)

    def test_fridge_excluded(self):
        from app.db import new_id
        c = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO categories (id, org_id, name, no_return, mark, sort) VALUES (?,?,?,?,?,?)",
                            (c, self.ctx.org_id, "ثلاجة", 0, "fridge", 1))
        i = self.item("Cold", 300)
        with self.db.transaction():
            self.db.execute("UPDATE items SET category_id=? WHERE id=?", (c, i))
        self.assertIn("Cold", self.names(500) + ["Cold"])
        self.assertNotIn("Cold", self.names(500))

    def test_no_coins_hint_from_last_close(self):
        self.cd.open(self.ctx, 0)
        self.assertFalse(change_fill.suggestions(self.db, self.ctx, 0)["no_pound_coins"])
        self.cd.close(self.ctx, 5000, denominations={"5000": 1}, note="تجربة")
        self.assertTrue(change_fill.suggestions(self.db, self.ctx, 0)["no_pound_coins"])
