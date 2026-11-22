"""U2-20 «مفيش فكة»: small in-stock items whose price fits in the change (1 to 10 EGP), nearest expiry first,
then margin, then fast sellers; never fridge, prescription, service or out-of-stock items; cost never returned;
a hint when the last close was counted with no one-pound coins."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from app.sales import change_fill
from tests.test_api import APICase, H
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
        self.assertIn("Cold", self.names(500))
        with self.db.transaction():
            self.db.execute("UPDATE items SET category_id=? WHERE id=?", (c, i))
        self.assertNotIn("Cold", self.names(500))

    def test_no_coins_hint_from_last_close(self):
        self.cd.open(self.ctx, 0)
        self.assertFalse(change_fill.suggestions(self.db, self.ctx, 0)["no_pound_coins"])
        self.cd.close(self.ctx, 5000, denominations={"5000": 1}, note="تجربة")
        self.assertTrue(change_fill.suggestions(self.db, self.ctx, 0)["no_pound_coins"])


class TestNoChangeSellable(TestNoChange):
    """Independent review of U2-20: a suggestion is always something the sale will sell, at the price shown."""
    def batch_of(self, item):
        return self.db.one("SELECT id FROM batches WHERE item_id=?", (item,))["id"]

    def test_only_what_the_sale_sells_at_the_shown_price(self):
        from app.db import new_id
        rec = self.item("Recalled", 300)
        hosp = self.item("Hospital", 300)
        dear = self.item("Batch dearer", 500)
        cheap = self.item("Batch cheaper", 1500)
        self.item("Plain", 400)
        self.db.conn.execute("PRAGMA foreign_keys=OFF")
        with self.db.transaction():
            self.db.execute("INSERT INTO recall_matches (id, org_id, notice_item_id, item_id, batch_id, match_basis,"
                            " qty_in_stock, status) VALUES (?,?,?,?,?,?,?,?)",
                            (new_id(), self.ctx.org_id, "x", rec, self.batch_of(rec), "batch_only", 5, "confirmed"))
            self.db.execute("UPDATE batches SET owner='hospital' WHERE id=?", (self.batch_of(hosp),))
            self.db.execute("UPDATE batches SET public_price=1500 WHERE id=?", (self.batch_of(dear),))
            self.db.execute("UPDATE batches SET public_price=500 WHERE id=?", (self.batch_of(cheap),))
        self.db.conn.execute("PRAGMA foreign_keys=ON")
        got = change_fill.suggestions(self.db, self.ctx, 600)["items"]
        self.assertEqual(sorted(x["name_en"] for x in got), ["Batch cheaper", "Plain"])
        for x in got:                                              # tapping it charges exactly the price shown
            sid = self.s.create(self.ctx)
            self.assertEqual(self.s.add_line(self.ctx, sid, x["id"], packs=1)["total"], x["price"])

    def test_hidden_items_only_for_who_may_see_them(self):
        from dataclasses import replace
        i = self.item("Hidden", 300)
        with self.db.transaction():
            self.db.execute("UPDATE items SET hidden=1 WHERE id=?", (i,))
        self.assertIn("Hidden", self.names(500))                  # the owner sees hidden items
        cashier = replace(self.ctx, permissions=frozenset(self.ctx.permissions - {"items.hidden"}))
        names = [x["name_en"] for x in change_fill.suggestions(self.db, cashier, 500)["items"]]
        self.assertNotIn("Hidden", names)

    def test_what_is_in_the_cart_is_not_offered_again(self):
        i = self.item("Last one", 300, qty=1)
        sid = self.s.create(self.ctx)
        self.assertIn("Last one", [x["name_en"] for x in change_fill.suggestions(self.db, self.ctx, 500, sid)["items"]])
        self.s.add_line(self.ctx, sid, i, packs=1)
        self.assertNotIn("Last one", [x["name_en"] for x in change_fill.suggestions(self.db, self.ctx, 500, sid)["items"]])

    def test_fast_seller_breaks_a_tie(self):
        a = self.item("Slow", 300, cost="1")
        b = self.item("Fast", 300, cost="1")
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, b, packs=2)
        self.cd.open(self.ctx, 0)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={})
        self.assertEqual(self.names(500)[:2], ["Fast", "Slow"])
        self.assertTrue(a)



class TestNoChangeRoute(APICase):
    def test_route_parses_and_refuses_bad_change(self):
        self.http_login()
        r = self.c.get("/api/sales/change-fill?change=500", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()), {"change", "items", "no_pound_coins"})
        self.assertEqual(self.c.get("/api/sales/change-fill?change=abc", headers=H).status_code, 422)
        self.assertEqual(self.c.get("/api/sales/change-fill?change=-1", headers=H).status_code, 422)
