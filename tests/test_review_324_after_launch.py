"""Codex review #324 parts 2-4, group D (after launch, owner decides): reproductions of the findings that
are verified but not fixed before launch. (S5/X11, S19, S28-S32 were fixed on 19/11: their tests moved to
tests/test_review_324_group_d.py.) Each test states the correct behaviour and is marked
expectedFailure: it fails today, and the suite turns red ("unexpected success") the day the defect is
fixed, so the mark is removed with the fix. Synthetic data only."""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import alternatives, gs1, service as cat
from app.inventory import service as inv
from app.procurement.service import Procurement
from app.purchasing import validation
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parent.parent
T = date.today()
known = unittest.expectedFailure


def _batch(db, ctx, item, qty, batch="B1", cost="15", days=400, mtype="purchase", expiry=True):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, batch, (T + timedelta(days=days)).isoformat() if expiry else None)
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type=mtype,
                          unit_cost=Decimal(cost))
    return b


class _Base(DBTestCase):
    def item(self, name="Tab", pack=1, price=1000, **kw):
        return cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": pack, "public_price": price, **kw})


# ---------------------------------------------------------------- part 2 (stock)
class TestS3Gs1LotWithoutSeparator(unittest.TestCase):
    @known
    def test_a_lot_containing_21_is_not_cut_into_lot_and_serial(self):
        p = gs1.parse("0106221025012345" "17290531" "10LOT21ABC")
        self.assertEqual(p["batch"], "LOT21ABC")


class TestS4UnknownStrength(_Base):
    @known
    def test_two_items_without_strength_are_not_exact_equivalents(self):
        a = self.item("Alpha", active_ingredient="paracetamol", dosage_form="tab")
        self.item("Beta", active_ingredient="paracetamol", dosage_form="tab")
        self.assertEqual(alternatives.for_item(self.db, self.ctx, a)["similar"], [])


class TestS7ImportStaffRedirectsStock(_Base):
    @known
    def test_a_row_decision_on_opening_stock_needs_inventory_adjust(self):
        from app.core import people
        from app.dataimport.service import DataImport
        from tests.test_dataimport import ITEMS, sheet
        di = DataImport(self.db)
        di.apply(self.ctx, di.analyze(self.ctx, sheet(ITEMS[:4]), "items.xlsx"))
        uid = people.add_user(self.db, self.ctx, username="importer", full_name="x", password="Staff-Pass-2026",
                              role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "items.import", "grant")
        sctx = self.login("importer", "Staff-Pass-2026")
        run = di.analyze(self.ctx, sheet([["كود", "الصنف", "الرصيد", "التكلفة"], [101, "Panadol", 3, 32]]),
                         "s.xlsx", kind="opening_stock")
        row = di.get(self.ctx, run)["rows"][0]
        other = self.db.one("SELECT id FROM items WHERE code='103'")["id"]
        with self.assertRaises(Exception):
            di.decide(sctx, run, row["id"], f"link:{other}")


class TestS22InferredNetRounding(unittest.TestCase):
    @known
    def test_inferred_net_uses_the_discount_of_rule(self):
        from app.sales.service import discount_of
        line = validation.derive_line({"public_price": 101, "discount_bp": 5000, "qty": 1,
                                 "field_sources": "{}", "field_confidence": "{}"})
        self.assertEqual(line["unit_net_price"], 101 - discount_of(101, 5000))


class TestS36ConsumptionRows(_Base):
    @known
    def test_two_rows_of_one_item_are_added_not_overwritten(self):
        import io
        from openpyxl import Workbook
        from app.procurement.service import import_consumption
        self.item("Cons", pack=3, code="C1")
        wb = Workbook()
        for r in (["كود", "الصنف", "الكمية"], ["C1", "Cons", 2], ["C1", "Cons", 4]):
            wb.active.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        import_consumption(self.db, self.ctx, buf.getvalue(), "c.xlsx", period_start="2026-10-01",
                           period_end="2026-10-31", dry_run=False)
        self.assertEqual(self.db.one("SELECT sum(qty_base) q FROM consumption_history")["q"], 18)


class TestS38ExpiredStockAndReorder(_Base):
    @known
    def test_expired_stock_does_not_count_as_on_hand(self):
        it = self.item("Expired Only", min_stock=12)
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "OLD", (T - timedelta(days=10)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=30, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("1"))
        ids = [s["item_id"] for s in Procurement(self.db).suggest(self.ctx)]
        self.assertIn(it, ids)


class TestS39ExpiryForecast(_Base):
    @known
    def test_shared_demand_is_spent_once_across_lots(self):
        it = self.item("Two Lots")
        _batch(self.db, self.ctx, it, 10, batch="A", days=10)
        _batch(self.db, self.ctx, it, 10, batch="B", days=10)
        Procurement(self.db).record_consumption(self.ctx, it, (T - timedelta(days=30)).isoformat(),
                                                (T - timedelta(days=1)).isoformat(), 30)
        rows = [e for e in Procurement(self.db).stock_health(self.ctx)["near_expiry"] if e["item_id"] == it]
        self.assertGreater(sum(e["units_at_risk"] for e in rows), 0)


class TestS40ExpiredAlternative(_Base):
    @known
    def test_an_expired_only_alternative_ranks_after_a_sellable_one(self):
        a = self.item("Expired Alt", price=1000)
        b = self.item("Good Alt", price=1000)
        with self.db.transaction():
            x = inv.get_or_create_batch(self.db, self.ctx, a, "OLD", (T - timedelta(days=5)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=a, qty_delta=5, batch_id=x, movement_type="purchase",
                              unit_cost=Decimal("1"))
        _batch(self.db, self.ctx, b, 5)
        rows = [dict(r) for r in self.db.all(f"SELECT {alternatives._COLS} FROM items WHERE id IN (?,?)", (a, b))]
        self.assertEqual(alternatives.rank(self.db, self.ctx, rows)[0]["id"], b)


if __name__ == "__main__":
    unittest.main()
