"""Supplier returns (مرتجع مشتريات): the paths the first tests did not walk. Without a batch the stock leaves
earliest expiry first at the average cost; every refusal leaves nothing behind (no stock, no account entry, no
slip number used); who may not see costs sees no values. Synthetic data only."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.inventory import service as inv
from app.procurement import returns, suppliers as sup
from app.sales.service import local_today
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class SupplierReturnPaths(Base):
    def setUp(self):
        super().setUp()
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.b1 = self.db.one("SELECT id FROM batches WHERE item_id=?", (self.pan,))["id"]      # 20 units, T+400
        with self.db.transaction():
            self.near = inv.get_or_create_batch(self.db, self.ctx, self.pan, "NEAR", (T + timedelta(days=20)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=4, batch_id=self.near,
                              movement_type="purchase", unit_cost=Decimal("18"))       # average now (300+72)/24 = 15.50
        self.day = local_today().isoformat()

    def batch(self, b):
        return inv.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, self.pan, b)

    def state(self):
        return (inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], sup.balance(self.db, self.ctx.org_id, self.sup),
                self.db.one("SELECT count(*) n FROM supplier_returns")["n"],
                self.db.one("SELECT count(*) n FROM stock_movements WHERE movement_type='supplier_return'")["n"])

    def test_without_a_batch_the_nearest_expiry_leaves_first_at_the_average_cost(self):
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.pan, None), 1550)
        r = returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "qty_base": 6, "reason": "overstock"}])
        self.assertEqual(r["total"], 6 * 1550)
        self.assertEqual((self.batch(self.near), self.batch(self.b1)), (0, 18))          # 4 from NEAR, then 2
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), -9300)
        moves = self.db.all("SELECT batch_id, qty_delta, ref_line_id FROM stock_movements WHERE movement_type='supplier_return'"
                            " ORDER BY rowid")
        self.assertEqual([(m["batch_id"], m["qty_delta"]) for m in moves], [(self.near, -4), (self.b1, -2)])
        self.assertEqual({m["ref_line_id"] for m in moves}, {r["lines"][0]["id"]})

    def test_more_than_the_stock_without_a_batch_is_refused_and_leaves_nothing(self):
        before = self.state()
        with self.assertRaises(Conflict) as e:
            returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "qty_base": 25, "reason": "expired"}])
        self.assertEqual(e.exception.code, "insufficient_stock")
        self.assertEqual(self.state(), before)
        r = returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "qty_base": 1, "reason": "expired"}])
        self.assertEqual(r["return_no"], 1)                                                  # the number was not used up

    def test_every_bad_line_is_refused_before_anything_moves(self):
        other = cat.create_item(self.db, self.ctx, {"name_en": "Other", "pack_size": 1, "public_price": 500})
        ok = {"item_id": self.pan, "batch_id": self.b1, "qty_base": 1, "reason": "expired"}
        cases = [
            (NotFound, "nope", [ok]),
            (ValidationFailed, self.sup, []),
            (ValidationFailed, self.sup, "not a list"),
            (ValidationFailed, self.sup, [ok] * (returns.MAX_LINES + 1)),
            (NotFound, self.sup, [{**ok, "item_id": "missing"}]),
            (NotFound, self.sup, [{**ok, "item_id": other}]),                               # batch of another item
            (ValidationFailed, self.sup, [{**ok, "qty_base": 0}]),
            (ValidationFailed, self.sup, [{**ok, "qty_base": -2}]),
            (ValidationFailed, self.sup, [{**ok, "qty_base": 1.5}]),
            (ValidationFailed, self.sup, [{**ok, "qty_base": "3"}]),
            (ValidationFailed, self.sup, [{**ok, "reason": "because"}]),
            (ValidationFailed, self.sup, [{**ok, "unit_value": -1}]),
            (ValidationFailed, self.sup, [{**ok, "unit_value": 12.5}]),
            (ValidationFailed, self.sup, [ok, {**ok, "qty_base": 0}]),                       # a bad second line too
        ]
        before = self.state()
        for exc, supplier, lines in cases:
            with self.subTest(lines=str(lines)[:80]):
                with self.assertRaises(exc):
                    returns.create(self.db, self.ctx, supplier, lines)
                self.assertEqual(self.state(), before)

    def test_an_item_with_no_known_cost_returns_at_zero_and_writes_no_account_entry(self):
        free = cat.create_item(self.db, self.ctx, {"name_en": "Sample", "pack_size": 1, "public_price": 100})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, free, "S", (T + timedelta(days=90)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=free, qty_delta=3, batch_id=b, movement_type="purchase",
                              unit_cost=None)
        self.assertEqual(returns.suggest_value(self.db, self.ctx, free, b), 0)
        r = returns.create(self.db, self.ctx, self.sup, [{"item_id": free, "batch_id": b, "qty_base": 3, "reason": "wrong_item"}])
        self.assertEqual(r["total"], 0)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_ledger WHERE supplier_id=?", (self.sup,))["n"], 0)

    def test_the_typed_value_wins_over_the_suggestion(self):
        r = returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "batch_id": self.b1, "qty_base": 2,
                                                          "reason": "damaged", "unit_value": 1200}])
        self.assertEqual(r["total"], 2400)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), -2400)

    def test_values_are_hidden_from_who_may_not_see_costs_and_the_list_filters_by_supplier(self):
        r = returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "batch_id": self.b1, "qty_base": 2,
                                                          "reason": "damaged"}])
        other = cat.create_supplier(self.db, self.ctx, name="مورد تاني")
        returns.create(self.db, self.ctx, other, [{"item_id": self.pan, "batch_id": self.b1, "qty_base": 1, "reason": "damaged"}])
        people.add_user(self.db, self.ctx, username="pharm1", full_name="صيدلي", password=PW, role_code="pharmacist")
        ph = self.login("pharm1", PW)
        got = returns.get(self.db, ph, r["id"])
        self.assertNotIn("total", got)
        self.assertTrue(all("unit_value" not in x for x in got["lines"]))
        lst = returns.listing(self.db, ph, self.day, self.day)
        self.assertIsNone(lst["total"])
        self.assertTrue(all("total" not in x for x in lst["rows"]))
        mine = returns.listing(self.db, self.ctx, self.day, self.day, supplier_id=self.sup)
        self.assertEqual([x["id"] for x in mine["rows"]], [r["id"]])
        self.assertEqual(mine["total"], 3000)
        with self.assertRaises(ValidationFailed):
            returns.listing(self.db, self.ctx, "yesterday", self.day)
        with self.assertRaises(NotFound):
            returns.get(self.db, self.ctx, "missing")

    def test_a_typed_value_needs_the_right_to_see_values(self):
        """A pharmacist may send stock back (purchases.approve) but sees no costs; the screen gives him no value
        field, and the server must not take a value from him either: it would credit the supplier's account
        with any amount. Without a typed value he still returns at the suggested value."""
        people.add_user(self.db, self.ctx, username="pharm2", full_name="صيدلي", password=PW, role_code="pharmacist")
        ph = self.login("pharm2", PW)
        before = self.state()
        with self.assertRaises(Exception) as e:
            returns.create(self.db, ph, self.sup, [{"item_id": self.pan, "batch_id": self.b1, "qty_base": 1,
                                                    "reason": "damaged", "unit_value": 999999}])
        self.assertEqual(getattr(e.exception, "code", None), "approval_required")
        self.assertEqual(self.state(), before)
        r = returns.create(self.db, ph, self.sup, [{"item_id": self.pan, "batch_id": self.b1, "qty_base": 1,
                                                    "reason": "damaged"}])
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), -1500)
        self.assertNotIn("total", r)
