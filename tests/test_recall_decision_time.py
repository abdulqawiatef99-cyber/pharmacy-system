"""A recalled lot that was handled and then received again is held from sale (#324 S1). The hold compares the
decision time with the receipt time at the ledger's millisecond precision. The decision time was written with
microseconds and rounded by SQLite to the NEAREST millisecond, while the ledger's own clock is cut DOWN to the
millisecond: a decision at .1236 s read as .124 and a receipt at .1238 s as .123, so the receipt looked older
than the decision and the recalled units were sellable (seen as a test failing now and then on CI).
Synthetic data only."""
from datetime import datetime, timezone
from unittest import mock

from app.catalog import service as cat
from app.db import new_id
from app.inventory import adjust, service as inv
from app.recalls.service import Recalls
from app.sales.service import Sales
from tests.helpers import DBTestCase

DECIDED = datetime(2031, 1, 5, 1, 2, 3, 123600, tzinfo=timezone.utc)      # .1236 s: rounds up to .124


class TestDecisionTimeIsCutToTheMillisecond(DBTestCase):
    def setUp(self):
        super().setUp()
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            self.batch = inv.get_or_create_batch(self.db, self.ctx, self.item, "AB01", "2030-01-01")
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=5, batch_id=self.batch, movement_type="purchase")
        self.r = Recalls(self.db)
        self.r.add_notice(self.ctx, title="سحب", items=[{"product_name": "Panadol 500mg", "batch_numbers": ["AB01"]}])
        self.m = self.r.alerts(self.ctx)[0]["id"]
        self.r.decide(self.ctx, self.m, "confirmed")
        adjust.adjust(self.db, self.ctx, self.item, set_to=0, batch_id=self.batch, reason="damaged", note="سحب")

    def receive_at(self, created_at: str, qty: int = 2):
        """A receipt whose ledger time is given (the ledger's clock cannot be set from a test)."""
        with self.db.transaction():
            self.db.execute("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta, movement_type,"
                            " created_by, created_at) VALUES (?,?,?,?,?,?,'purchase',?,?)",
                            (new_id(), self.ctx.org_id, self.ctx.branch_id, self.item, self.batch, qty, self.ctx.user_id, created_at))
            self.db.execute("UPDATE stock_balances SET qty=qty+? WHERE org_id=? AND item_id=? AND batch_key=?",
                            (qty, self.ctx.org_id, self.item, self.batch))

    def held(self):
        rows = Sales(self.db)._stock_rows(self.ctx, self.ctx.branch_id, self.item)
        return [bool(r["recalled"]) for r in rows]

    def test_a_receipt_in_the_same_millisecond_as_the_decision_is_held(self):
        with mock.patch("app.core.accounts.utcnow", return_value=DECIDED):
            self.r.decide(self.ctx, self.m, "handled")
        self.receive_at("2031-01-05T01:02:03.123Z")                 # the ledger's clock at .1238 s, cut to .123
        self.assertEqual(self.held(), [True])

    def test_the_decision_time_is_stored_at_millisecond_precision(self):
        with mock.patch("app.core.accounts.utcnow", return_value=DECIDED):
            self.r.decide(self.ctx, self.m, "handled")
        at = self.db.one("SELECT decided_at, strftime('%Y-%m-%dT%H:%M:%fZ', decided_at) ms FROM recall_matches WHERE id=?", (self.m,))
        self.assertEqual(at["decided_at"], "2031-01-05T01:02:03.123000Z")
        self.assertEqual(at["ms"], "2031-01-05T01:02:03.123Z")

    def test_a_receipt_before_the_decision_stays_free(self):
        self.receive_at("2031-01-05T01:02:03.122Z")                 # before it was handled: part of what was handled
        with mock.patch("app.core.accounts.utcnow", return_value=DECIDED):
            self.r.decide(self.ctx, self.m, "handled")
        self.assertEqual(self.held(), [False])

    def test_reopening_by_matching_uses_the_same_clock(self):
        with mock.patch("app.core.accounts.utcnow", return_value=DECIDED):
            self.r.decide(self.ctx, self.m, "handled")
            self.receive_at("2031-01-05T01:02:03.123Z")
            self.r.match_new_batches(self.ctx)
        at = self.db.one("SELECT status, decided_at FROM recall_matches WHERE id=?", (self.m,))
        self.assertEqual((at["status"], at["decided_at"]), ("confirmed", "2031-01-05T01:02:03.123000Z"))


if __name__ == "__main__":
    import unittest
    unittest.main()
