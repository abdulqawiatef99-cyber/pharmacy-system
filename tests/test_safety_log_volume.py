"""The safety log must not be filled by one constant notice. Measured on a synthetic medium pharmacy (scale.py,
one year, 81,812 sales): 440,696 of the 464,792 safety_events rows were "no verified ingredient data for item
X", written for every line of every safety check, about a quarter of a 1 GB database (#304 B10). The notice
is still shown on every sale; it is recorded the first time it is shown for an item in a month. Every other
finding and every decision is recorded every time, as before. Synthetic data only."""
from datetime import date
from unittest import mock

from app.catalog import service as cat
from app.safety import engine
from app.safety.engine import SafetyEngine
from tests.helpers import DBTestCase


class TestConstantNoticeLoggedOnceAMonth(DBTestCase):
    def setUp(self):
        super().setUp()
        engine.forget_logged_notices()
        self.se = SafetyEngine(self.db)
        mk = lambda n: cat.create_item(self.db, self.ctx, {"name_en": n}, force=True)   # noqa: E731
        self.a, self.b = mk("Unmapped Syrup 100ml"), mk("Unmapped Cream 30g")

    def rows(self, code="insufficient_data"):
        return self.db.one("SELECT count(*) n FROM safety_events WHERE finding_code=?", (code,))["n"]

    def shown(self, items, **kw):
        return [f.code for f in self.se.check(self.ctx, [{"item_id": i} for i in items], context_type="sale", **kw)]

    def test_the_notice_is_shown_every_time_and_recorded_once(self):
        for n in range(5):
            self.assertIn("insufficient_data", self.shown([self.a], context_id=f"sale-{n}"))
        self.assertEqual(self.rows(), 1)
        row = self.db.one("SELECT user_id, context_id, decision FROM safety_events WHERE finding_code='insufficient_data'")
        self.assertEqual((row["user_id"], row["context_id"], row["decision"]), (self.ctx.user_id, "sale-0", "shown"))

    def test_each_item_has_its_own_record(self):
        self.shown([self.a, self.b])
        self.shown([self.b])
        self.assertEqual(self.rows(), 2)

    def test_a_new_month_records_it_again(self):
        self.shown([self.a])
        with mock.patch.object(engine, "_today", return_value=date(2031, 2, 1)):
            self.shown([self.a])
            self.shown([self.a])
        self.assertEqual(self.rows(), 2)

    def test_another_pharmacy_has_its_own_record(self):
        _, other = self.second_tenant()
        theirs = cat.create_item(self.db, other, {"name_en": "Unmapped Syrup 100ml"}, force=True)
        self.shown([self.a])
        SafetyEngine(self.db).check(other, [{"item_id": theirs}], context_type="sale")
        self.assertEqual(self.rows(), 2)

    def test_a_restart_records_it_at_most_once_more(self):
        self.shown([self.a])
        engine.forget_logged_notices()                    # what a restart of the program does
        self.shown([self.a])
        self.shown([self.a])
        self.assertEqual(self.rows(), 2)

    def test_findings_that_ask_for_a_decision_are_recorded_every_time(self):
        """Only the constant notice is thinned: a real warning is written each time it is shown."""
        for i in (self.a, self.b):
            self.se.set_item_ingredients(self.ctx, i, [("Testium", "500mg")])
            self.se.verify_item_ingredients(self.ctx, i)
        for _ in range(3):
            self.assertIn("duplicate_ingredient", self.shown([self.a, self.b]))
        self.assertEqual(self.rows("duplicate_ingredient"), 3)
        self.assertEqual(self.rows(), 0)

    def test_a_check_that_is_not_logged_does_not_use_up_the_record(self):
        self.shown([self.a], log=False)
        self.assertEqual(self.rows(), 0)
        self.shown([self.a])
        self.assertEqual(self.rows(), 1)

    def test_a_record_that_was_rolled_back_is_written_next_time(self):
        try:
            with self.db.transaction():
                self.shown([self.a])
                raise RuntimeError("the sale failed")
        except RuntimeError:
            pass
        self.assertEqual(self.rows(), 0)
        self.shown([self.a])
        self.assertEqual(self.rows(), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
