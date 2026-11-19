"""Speed with years of data (F104, F59, #304 B6/B7/B9; #324 R8, R9). The slow screens asked the database one or
more questions per item or per customer, some of them without the pharmacy id their index starts with, so each
question read a whole ledger. These tests pin: the number of questions does not grow with the catalogue, each
ledger is read through its index, and the answers are the same as before. Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from app.procurement.service import Procurement
from app.reports import owner
from app.sales.service import local_today
from tests.test_cashier_acceptance import Base

T = date.today()


def plan(db, sql, params):
    return " | ".join(r["detail"] for r in db.all("EXPLAIN QUERY PLAN " + sql, params))


class _Queries:
    """Records every statement a call sends through db.all / db.one."""

    def __init__(self, db):
        self.db, self.seen = db, []

    def __enter__(self):
        all_, one = self.db.all, self.db.one
        self.db.all = lambda sql, *a, **k: (self.seen.append((sql, a[0] if a else ())), all_(sql, *a, **k))[1]
        self.db.one = lambda sql, *a, **k: (self.seen.append((sql, a[0] if a else ())), one(sql, *a, **k))[1]
        return self

    def __exit__(self, *exc):
        del self.db.all, self.db.one

    def find(self, needle):
        return next(q for q in self.seen if needle in q[0])


def assert_indexed(case, p, table_alias):
    case.assertNotRegex(p, rf"SCAN {table_alias}\b", p)


# ---------------------------------------------------------------- R8 / B7: unsold items
class TestUnsoldItems(Base):
    def setUp(self):
        super().setUp()
        self.day = local_today().isoformat()
        self.cd.open(self.ctx, 0)
        self.other = cat.create_item(self.db, self.ctx, {"name_en": "Slow Mover", "pack_size": 1, "public_price": 900})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.other, "S1", (T + timedelta(days=50)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.other, qty_delta=7, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("4"))

    def ids(self, r):
        return [x["item_id"] for x in r["rows"]]

    def test_only_items_in_stock_without_a_sale_in_the_period(self):
        self.sell(units=2)                                                  # Panadol sold today
        r = owner.unsold_items(self.db, self.ctx, self.day, self.day)
        self.assertEqual(self.ids(r), [self.other])
        row = r["rows"][0]
        self.assertEqual((row["qty"], row["price_value"], row["cost_value"], row["last_sold"]), (7, 6300, 2800, None))
        self.assertTrue(row["last_in"])
        self.assertEqual(row["expiry"], (T + timedelta(days=50)).isoformat())
        self.assertEqual(r["totals"], {"n": 1, "price_value": 6300, "cost_value": 2800, "unknown_cost": 0})

    def test_a_sale_outside_the_period_gives_the_last_sold_date(self):
        s = self.sell(units=2)
        old = "2026-01-10T10:00:00.000000Z"
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (old, s["id"]))
        r = owner.unsold_items(self.db, self.ctx, self.day, self.day)
        rows = {x["item_id"]: x for x in r["rows"]}
        self.assertEqual(set(rows), {self.pan, self.other})
        self.assertEqual(rows[self.pan]["last_sold"], old)

    def test_a_voided_sale_is_not_a_sale(self):
        s = self.sell(units=2)
        self.s.void(self.ctx, s["id"], "خطأ في الفاتورة")
        r = owner.unsold_items(self.db, self.ctx, self.day, self.day)
        rows = {x["item_id"]: x for x in r["rows"]}
        self.assertIn(self.pan, rows)
        self.assertIsNone(rows[self.pan]["last_sold"])

    def test_an_item_with_no_stock_or_hidden_from_this_person_is_left_out(self):
        with self.db.transaction():
            self.db.execute("UPDATE items SET hidden=1 WHERE id=?", (self.other,))
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=-20,
                              batch_id=self.db.one("SELECT id FROM batches WHERE item_id=?", (self.pan,))["id"],
                              movement_type="adjustment")
        self.assertEqual(self.ids(owner.unsold_items(self.db, self.ctx, self.day, self.day)), [self.other])   # the owner sees hidden
        from app.core import people
        uid = people.add_user(self.db, self.ctx, username="manager1", full_name="m", password="Staff-Pass-2026", role_code="manager")
        people.set_exception(self.db, self.ctx, uid, "items.hidden", "deny")
        self.assertEqual(self.ids(owner.unsold_items(self.db, self.login("manager1", "Staff-Pass-2026"), self.day, self.day)), [])

    def test_a_long_period_gives_the_same_answer_by_its_own_path(self):
        """Over 62 days the question is asked item by item (first sale found ends it); same rows either way."""
        s = self.sell(units=2)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at='2026-03-10T10:00:00.000000Z' WHERE id=?", (s["id"],))
        for d1, d2, expect in (("2026-03-01", "2026-03-31", {self.other}),                # short, sold inside
                               ("2026-01-01", "2026-12-31", {self.other}),                # long, sold inside
                               ("2026-04-01", "2026-04-30", {self.pan, self.other}),      # short, sold before
                               ("2026-04-01", "2026-12-31", {self.pan, self.other})):     # long, sold before
            with self.subTest(d1=d1, d2=d2):
                r = owner.unsold_items(self.db, self.ctx, d1, d2)
                self.assertEqual(set(self.ids(r)), expect)
                rows = {x["item_id"]: x for x in r["rows"]}
                if self.pan in rows:
                    self.assertEqual(rows[self.pan]["last_sold"], "2026-03-10T10:00:00.000000Z")

    def test_the_long_path_reads_each_items_own_lines(self):
        with _Queries(self.db) as q:
            owner.unsold_items(self.db, self.ctx, "2026-01-01", "2026-12-31")
        p = plan(self.db, *q.find("NOT EXISTS"))
        self.assertIn("ix_sale_lines_item (item_id=?)", p, p)
        for alias in ("l", "s", "m"):
            assert_indexed(self, p, alias)

    def test_the_number_of_questions_does_not_grow_with_the_catalogue(self):
        def questions():
            with _Queries(self.db) as q:
                owner.unsold_items(self.db, self.ctx, self.day, self.day)
            return len(q.seen)
        few = questions()
        for i in range(40):
            it = cat.create_item(self.db, self.ctx, {"name_en": f"Bulk Item {i}", "pack_size": 1, "public_price": 500})
            with self.db.transaction():
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=3, movement_type="opening", unit_cost=Decimal("2"))
        self.assertEqual(questions(), few)
        self.assertLessEqual(few, 8)

    def test_every_ledger_is_read_through_an_index(self):
        self.sell(units=2)
        with _Queries(self.db) as q:
            owner.unsold_items(self.db, self.ctx, "2026-01-01", "2026-01-31")
        for sql, params in q.seen:
            p = plan(self.db, sql, params)
            for alias in ("m", "l", "s", "stock_movements", "sale_lines", "sales"):
                assert_indexed(self, p, alias)
        self.assertIn("ix_sales_completed", plan(self.db, *q.find("DISTINCT l.item_id")))


# ---------------------------------------------------------------- R9 / B9: customer sales
class TestCustomerSales(Base):
    def setUp(self):
        super().setUp()
        self.day = local_today().isoformat()
        self.cd.open(self.ctx, 0)
        self.cust = self.s.create_customer(self.ctx, name="عميل تجربة", credit_limit=100000)
        self.sell(units=2, credit=4000, customer=self.cust)

    def test_the_balance_is_read_from_the_customers_own_ledger_rows(self):
        with _Queries(self.db) as q:
            r = owner.customer_sales(self.db, self.ctx, self.day, self.day)
        self.assertEqual([(x["customer_id"], x["balance"]) for x in r["rows"]], [(self.cust, 4000)])
        p = plan(self.db, *q.find("customer_ledger"))
        self.assertNotIn("SCAN customer_ledger", p, p)
        self.assertIn("ix_customer_ledger (org_id=? AND customer_id=?)", p, p)

    def test_another_pharmacys_ledger_row_is_not_added_to_a_balance(self):
        _, other = self.second_tenant()
        with self.db.transaction():      # a row of another pharmacy carrying this customer's id by mistake
            self.db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind) VALUES ('x1', ?, ?, 999,"
                            " 'adjustment')", (other.org_id, self.cust))
        self.assertEqual(owner.customer_sales(self.db, self.ctx, self.day, self.day)["rows"][0]["balance"], 4000)

    def test_walk_in_sales_have_no_balance(self):
        self.sell(units=2)
        r = owner.customer_sales(self.db, self.ctx, self.day, self.day)
        self.assertIsNone(r["walk_in"]["balance"])
        self.assertEqual(r["totals"]["n"], 2)


# ---------------------------------------------------------------- B6: order suggestions and stock health
class TestProcurementInOnePass(Base):
    def setUp(self):
        super().setUp()
        self.p = Procurement(self.db)
        self.items = []
        for i in range(12):
            it = cat.create_item(self.db, self.ctx, {"name_en": f"Bulk {i:02d}", "pack_size": 1 + i % 3, "public_price": 500,
                                                     "min_stock": 6 if i % 2 else 0})
            self.items.append(it)
            with self.db.transaction():
                if i % 4:
                    b = inv.get_or_create_batch(self.db, self.ctx, it, f"L{i}", (T + timedelta(days=20 * i - 30)).isoformat())
                    inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5 + i, batch_id=b, movement_type="purchase",
                                      unit_cost=Decimal("2"))
                if i % 3 == 0:
                    inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=4, movement_type="opening")
                if i % 5 == 1:
                    inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=-2, movement_type="sale", allow_negative=True)
            if i % 2 == 0:
                self.p.record_consumption(self.ctx, it, (T - timedelta(days=60)).isoformat(), (T - timedelta(days=31)).isoformat(), 10 + i)
            if i % 6 == 0:
                self.p.record_consumption(self.ctx, it, (T - timedelta(days=30)).isoformat(), (T - timedelta(days=1)).isoformat(), 40)

    def per_item(self, window=90):
        """The figures item by item, the way every item used to be asked."""
        return {it: (self.p._usage(self.ctx, it, window), self.p._sellable(self.ctx, it), self.p._on_order(self.ctx, it))
                for it in self.items + [self.pan]}

    def test_suggestions_equal_the_item_by_item_figures(self):
        ref = self.per_item()
        got = {s["item_id"]: s for s in self.p.suggest(self.ctx, only_needed=False)}
        self.assertEqual(set(got), set(ref))
        for it, (u, (on_hand, expired), on_order) in ref.items():
            s = got[it]
            self.assertEqual((s["on_hand"], s["data"]["expired"], s["data"]["on_order"]), (on_hand, expired, on_order), it)
            self.assertAlmostEqual(s["daily_usage"], round(u["daily"], 3), places=3)
            self.assertEqual(s["data"]["usage"], {k: v for k, v in u.items() if k != "rates"})

    def test_suggestions_ask_a_fixed_number_of_questions(self):
        with _Queries(self.db) as q:
            self.p.suggest(self.ctx, only_needed=False)
        self.assertLessEqual(len(q.seen), 6, [s[:70] for s, _ in q.seen])

    def test_stock_health_asks_a_fixed_number_of_questions_and_keeps_its_answer(self):
        ref = self.per_item(90)
        with _Queries(self.db) as q:
            h = self.p.stock_health(self.ctx)
        self.assertLessEqual(len(q.seen), 6, [s[:70] for s, _ in q.seen])
        slow = {x["item_id"] for x in h["slow_movers"]}
        expect_slow = {it for it, (u, _, _) in ref.items()
                       if u["daily"] == 0 and self.p._on_hand(self.ctx, it) > 0}
        self.assertEqual(slow, expect_slow)
        self.assertTrue(h["near_expiry"])
        self.assertEqual([e["days_left"] for e in h["near_expiry"]], sorted(e["days_left"] for e in h["near_expiry"]))

    def test_the_sales_of_the_window_are_read_through_an_index(self):
        with _Queries(self.db) as q:
            self.p.suggest(self.ctx)
        p = plan(self.db, *q.find("movement_type IN ('sale','sale_return')"))
        self.assertNotIn("SCAN stock_movements", p, p)
        self.assertIn("ix_moves_type_time", p, p)


if __name__ == "__main__":
    import unittest
    unittest.main()
