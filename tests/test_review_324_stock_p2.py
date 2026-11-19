"""#324 parts 2, the stock P2s checked and fixed on 19/11: S3 (a lot containing "21" read without the GS
separator), S4 (unknown strength is not "the same strength"), S36 (two consumption rows of one item),
S37 (imported and local demand over different days), S38 (expired stock is not on hand), S39 (one demand
spent once across lots), S40 (an expired-only alternative). Synthetic data only."""
import io
import unittest
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import Workbook

from app.catalog import alternatives, gs1, service as cat
from app.inventory import service as inv
from app.procurement.service import Procurement, import_consumption
from tests.helpers import DBTestCase

T = date.today()
GTIN = "0106221025012345"
G = "\x1d"


def _batch(db, ctx, item, qty, batch="B1", cost="15", days=400):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, batch, (T + timedelta(days=days)).isoformat())
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type="purchase",
                          unit_cost=Decimal(cost))
    return b


class _Base(DBTestCase):
    def item(self, name="Tab", pack=1, price=1000, **kw):
        return cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": pack, "public_price": price, **kw})


# ---------------------------------------------------------------- S3
class TestS3Gs1LotWithoutSeparator(unittest.TestCase):
    def test_a_lot_containing_21_is_not_cut_into_lot_and_serial(self):
        p = gs1.parse(GTIN + "17290531" + "10LOT21ABC")
        self.assertEqual((p["batch"], p.get("serial")), ("LOT21ABC", None))

    def test_the_printed_form_with_brackets_marks_every_field(self):
        """(10)LOT21ABC as printed under the code: the brackets are the separators, nothing is guessed."""
        p = gs1.parse("(01)06221025012345(17)290531(10)LOT21ABC")
        self.assertEqual((p["batch"], p.get("serial"), p["expiry"]), ("LOT21ABC", None, "2029-05-31"))
        p = gs1.parse("(01)06221025012345(10)LOT21ABCDEFGH(21)SERIAL21XYZ(17)290531")
        self.assertEqual((p["batch"], p["serial"], p["expiry"]), ("LOT21ABCDEFGH", "SERIAL21XYZ", "2029-05-31"))
        self.assertNotIn("guessed", p)

    def test_a_real_serial_after_the_lot_is_still_split(self):
        p = gs1.parse(GTIN + "17290531" + "10AB123" + "21SERIAL0001")
        self.assertEqual((p["batch"], p["serial"]), ("AB123", "SERIAL0001"))

    def test_a_split_decided_without_a_separator_is_flagged(self):
        """The caller can ask the person to check the lot: both readings are possible."""
        self.assertTrue(gs1.parse(GTIN + "17290531" + "10AB123" + "21SERIAL0001").get("guessed"))
        self.assertTrue(gs1.parse(GTIN + "17290531" + "10LOT21ABC").get("guessed"))
        self.assertNotIn("guessed", gs1.parse(GTIN + "17290531" + "10AB123" + G + "21SERIAL0001"))
        self.assertNotIn("guessed", gs1.parse(GTIN + "17290531" + "10AB123"))


# ---------------------------------------------------------------- S4
class TestS4UnknownStrength(_Base):
    def test_two_items_without_strength_are_not_exact_equivalents(self):
        a = self.item("Alpha", active_ingredient="paracetamol", dosage_form="tab")
        b = self.item("Beta", active_ingredient="paracetamol", dosage_form="tab")
        r = alternatives.for_item(self.db, self.ctx, a)
        self.assertEqual(r["similar"], [])
        self.assertEqual([x["id"] for x in r["other_strength"]], [b])
        self.assertIn("strength", r["missing"])

    def test_the_same_written_strength_is_still_an_equivalent(self):
        a = self.item("Alpha 500mg", active_ingredient="paracetamol", dosage_form="tab", strength="500 mg")
        b = self.item("Beta 500mg", active_ingredient="paracetamol", dosage_form="tab", strength="500mg")
        self.assertEqual([x["id"] for x in alternatives.for_item(self.db, self.ctx, a)["similar"]], [b])


# ---------------------------------------------------------------- S36
class TestS36ConsumptionRows(_Base):
    def sheet(self, rows):
        wb = Workbook()
        for r in rows:
            wb.active.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_two_rows_of_one_item_are_added_not_overwritten(self):
        self.item("Cons", pack=3, code="C1")
        data = self.sheet([["كود", "الصنف", "الكمية"], ["C1", "Cons", 2], ["C1", "Cons", 4]])
        r = import_consumption(self.db, self.ctx, data, "c.xlsx", period_start="2026-10-01",
                               period_end="2026-10-31", dry_run=False)
        self.assertEqual(r["total_base_units"], 18)
        self.assertEqual(self.db.one("SELECT sum(qty_base) q, count(*) n FROM consumption_history")["q"], 18)

    def test_importing_the_same_file_again_replaces_it_does_not_double(self):
        self.item("Cons", pack=3, code="C1")
        data = self.sheet([["كود", "الصنف", "الكمية"], ["C1", "Cons", 2], ["C1", "Cons", 4]])
        for _ in range(2):
            import_consumption(self.db, self.ctx, data, "c.xlsx", period_start="2026-10-01",
                               period_end="2026-10-31", dry_run=False)
        self.assertEqual(self.db.one("SELECT sum(qty_base) q FROM consumption_history")["q"], 18)


# ---------------------------------------------------------------- S37
class TestS37MixedDemand(_Base):
    def test_imported_and_local_sales_are_divided_by_all_their_days(self):
        """30 units imported for the first 30 days of the window, 60 sold here in the next 60: one a day,
        not 90 units over the 30 imported days."""
        it = self.item("Mixed Demand")
        p = Procurement(self.db)
        p.record_consumption(self.ctx, it, (T - timedelta(days=90)).isoformat(), (T - timedelta(days=61)).isoformat(), 30)
        _batch(self.db, self.ctx, it, 200)
        with self.db.transaction():
            m = inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=-60, batch_id=None, movement_type="sale",
                                  allow_negative=True)
        u = p._usage(self.ctx, it, 90)
        self.assertEqual(u["days_covered"], 90)
        self.assertAlmostEqual(u["daily"], 1.0, places=2)
        self.assertTrue(m)

    def test_history_alone_keeps_its_own_days(self):
        it = self.item("History Only")
        p = Procurement(self.db)
        p.record_consumption(self.ctx, it, (T - timedelta(days=30)).isoformat(), (T - timedelta(days=1)).isoformat(), 60)
        self.assertAlmostEqual(p._usage(self.ctx, it, 90)["daily"], 2.0, places=2)

    def test_local_sales_alone_keep_the_whole_window(self):
        it = self.item("Local Only")
        _batch(self.db, self.ctx, it, 200)
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=-90, batch_id=None, movement_type="sale",
                              allow_negative=True)
        self.assertAlmostEqual(Procurement(self.db)._usage(self.ctx, it, 90)["daily"], 1.0, places=2)


# ---------------------------------------------------------------- S38
class TestS38ExpiredStockAndReorder(_Base):
    def expired(self, it, qty=30):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "OLD", (T - timedelta(days=10)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=qty, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("1"))

    def test_expired_stock_does_not_count_as_on_hand(self):
        it = self.item("Expired Only", min_stock=12)
        self.expired(it)
        s = [x for x in Procurement(self.db).suggest(self.ctx) if x["item_id"] == it]
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["on_hand"], s[0]["suggested_base"], s[0]["data"]["expired"]), (0, 12, 30))
        self.assertTrue(any("منتهي" in r for r in s[0]["reasons"]))

    def test_sellable_stock_above_the_minimum_needs_nothing(self):
        it = self.item("Enough", min_stock=12)
        self.expired(it, 5)
        _batch(self.db, self.ctx, it, 20, batch="NEW")
        self.assertEqual([x for x in Procurement(self.db).suggest(self.ctx) if x["item_id"] == it], [])

    def test_stock_without_a_date_counts(self):
        it = self.item("Undated", min_stock=12)
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, movement_type="opening")
        self.assertEqual([x for x in Procurement(self.db).suggest(self.ctx) if x["item_id"] == it], [])


# ---------------------------------------------------------------- S39
class TestS39ExpiryForecast(_Base):
    def test_shared_demand_is_spent_once_across_lots(self):
        it = self.item("Two Lots")
        _batch(self.db, self.ctx, it, 10, batch="A", days=10)
        _batch(self.db, self.ctx, it, 10, batch="B", days=10)
        Procurement(self.db).record_consumption(self.ctx, it, (T - timedelta(days=30)).isoformat(),
                                                (T - timedelta(days=1)).isoformat(), 30)
        rows = [e for e in Procurement(self.db).stock_health(self.ctx)["near_expiry"] if e["item_id"] == it]
        self.assertEqual(sum(e["units_at_risk"] for e in rows), 10)
        self.assertEqual(sum(e["expected_to_sell"] for e in rows), 10)

    def test_a_later_lot_sells_what_the_days_between_allow(self):
        it = self.item("Staggered")
        _batch(self.db, self.ctx, it, 10, batch="A", days=10)
        _batch(self.db, self.ctx, it, 10, batch="B", days=15)
        Procurement(self.db).record_consumption(self.ctx, it, (T - timedelta(days=30)).isoformat(),
                                                (T - timedelta(days=1)).isoformat(), 30)
        rows = {e["batch_no"]: e for e in Procurement(self.db).stock_health(self.ctx)["near_expiry"] if e["item_id"] == it}
        self.assertEqual((rows["A"]["units_at_risk"], rows["B"]["units_at_risk"]), (0, 5))


# ---------------------------------------------------------------- S40
class TestS40ExpiredAlternative(_Base):
    def setUp(self):
        super().setUp()
        self.a = self.item("Expired Alt", price=1000)
        self.b = self.item("Good Alt", price=1000)
        with self.db.transaction():
            x = inv.get_or_create_batch(self.db, self.ctx, self.a, "OLD", (T - timedelta(days=5)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.a, qty_delta=5, batch_id=x, movement_type="purchase",
                              unit_cost=Decimal("1"))
        _batch(self.db, self.ctx, self.b, 5)

    def rows(self):
        return [dict(r) for r in self.db.all(f"SELECT {alternatives._COLS} FROM items WHERE id IN (?,?)", (self.a, self.b))]

    def test_an_expired_only_alternative_ranks_after_a_sellable_one(self):
        out = alternatives.rank(self.db, self.ctx, self.rows())
        self.assertEqual([r["id"] for r in out], [self.b, self.a])
        bad = out[1]
        self.assertEqual((bad["stock_qty"], bad["expired_qty"], bad["expiry_soon"]), (0, 5, False))

    def test_a_sellable_lot_beside_an_expired_one_still_counts(self):
        _batch(self.db, self.ctx, self.a, 2, batch="NEW", days=30)
        out = {r["id"]: r for r in alternatives.rank(self.db, self.ctx, self.rows())}
        self.assertEqual((out[self.a]["stock_qty"], out[self.a]["expired_qty"]), (2, 5))
        self.assertEqual(out[self.a]["next_expiry"], (T + timedelta(days=30)).isoformat())


if __name__ == "__main__":
    unittest.main()
