"""Codex review #324 parts 2-4, group D (after launch, owner decides): reproductions of the findings that
are verified but not fixed before launch. (S5/X11, S19, S28-S32 were fixed on 19/11: their tests moved to
tests/test_review_324_group_d.py; S3, S4, S36-S40 the same day:
tests/test_review_324_stock_p2.py.) Each test states the correct behaviour and is marked
expectedFailure: it fails today, and the suite turns red ("unexpected success") the day the defect is
fixed, so the mark is removed with the fix. Synthetic data only."""
import unittest

from app.purchasing import validation
from tests.helpers import DBTestCase

known = unittest.expectedFailure


# ---------------------------------------------------------------- part 2 (stock)
class TestS7ImportStaffRedirectsStock(DBTestCase):
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


if __name__ == "__main__":
    unittest.main()
