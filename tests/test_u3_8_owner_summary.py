"""U3-8 the owner's shift summary: closed shifts only, figures from the shift report, out-of-stock items, owner /
manager only; the program sends nothing."""
from dataclasses import replace

from app.core.errors import Conflict, Forbidden
from app.reports.owner_summary import shift_summary
from app.sales.cash import CashDrawer
from tests.test_sales import SalesCase


class TestOwnerSummary(SalesCase):
    def test_text(self):
        cd = CashDrawer(self.db)
        shift = self.db.one("SELECT id FROM cash_shifts WHERE status='open'")["id"]
        with self.assertRaises(Conflict):
            shift_summary(self.db, self.ctx, shift)
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=13)           # sells it out
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        cd.close(self.ctx, counted_cash=50000 + d["total"] - 100, note="عدّ تجربة", float_left=0)
        r = shift_summary(self.db, self.ctx, shift)
        self.assertIn("1 فاتورة", r["text"])
        self.assertIn("عجز 1.00 ج", r["text"])
        self.assertIn("Panadol 500mg 24 tab", r["out_of_stock"])
        with self.assertRaises(Forbidden):
            shift_summary(self.db, replace(self.ctx, permissions=self.ctx.permissions - {"cash.see_expected"}), shift)


class TestCodexOn422(TestOwnerSummary):
    def test_hidden_item_not_named_without_the_right(self):
        shift = self.db.one("SELECT id FROM cash_shifts WHERE status='open'")["id"]
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=13)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        CashDrawer(self.db).close(self.ctx, counted_cash=50000 + d["total"], note="عدّ تجربة", float_left=0)
        with self.db.transaction():
            self.db.execute("UPDATE items SET hidden=1 WHERE id=?", (self.pan,))
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"items.hidden"})
        self.assertNotIn("Panadol 500mg 24 tab", shift_summary(self.db, staff, shift)["out_of_stock"])
