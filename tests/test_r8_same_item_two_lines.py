"""The same item on two lines (one with a discount) across two batches with different prices: the screen
priced both lines from the earliest batch, the sale took the second line from the next batch at its own
price, so the totals differed and the invoice could never be completed ("price changed" every time)."""
from app.inventory import service as inv
from tests.test_sales import SalesCase


class TestSameItemTwoLines(SalesCase):
    def test_second_line_is_priced_from_the_batch_it_leaves_from(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=3)                       # all of OLD (19.00 a unit)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1, discount_bp=1000)  # so this one is NEW (20.00)
        by_disc = {l["discount_bp"]: l for l in d["lines"]}
        self.assertEqual([(a["batch_no"], a["qty"]) for a in by_disc[0]["allocations"]], [("OLD", 3)])
        self.assertEqual([(a["batch_no"], a["qty"], a["unit_price"]) for a in by_disc[1000]["allocations"]],
                         [("NEW", 1, 2000)])
        self.assertEqual(d["total"], 3 * 1900 + 2000 - 200)
        done = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual((done["status"], done["total"]), ("completed", d["total"]))
        st = inv.stock_for_item(self.db, self.ctx, self.pan)
        self.assertEqual({b["batch_no"]: b["qty"] for b in st["batches"]}, {"NEW": 9})
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_two_lines_cannot_preview_more_than_the_stock(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=10)
        d = self.s.add_line(self.ctx, sid, self.pan, units=3, discount_bp=1000)
        got = sorted((a["batch_no"], a["qty"]) for l in d["lines"] for a in l["allocations"])
        self.assertEqual(got, [("NEW", 3), ("NEW", 7), ("OLD", 3)])
        self.assertFalse(any(l.get("problem") for l in d["lines"]))
