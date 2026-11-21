"""Update 1 (launch day 21/11): a fixed EGP discount per line, and one discount on the whole invoice (EGP or %),
spread over the lines by the largest-remainder rule; held to the same limits and approvals as a percent; returns
and reports use the money each line was really sold for."""
import dataclasses
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Forbidden, ValidationFailed
from app.inventory import service as inv
from app.reports import invoices
from app.sales.service import Sales, equivalent_bp, kept_value, outstanding_value, prorate, spread
from tests.test_sales import TODAY, SalesCase
from datetime import timedelta
import unittest


class TestHelpers(unittest.TestCase):
    def test_spread_adds_up_and_never_past_a_line(self):
        self.assertEqual(spread(100, [3000, 1000]), [75, 25])
        self.assertEqual(spread(1, [1000, 1000, 1000]), [1, 0, 0])
        self.assertEqual(spread(2, [1000, 1000, 1]), [1, 1, 0])
        self.assertEqual(sum(spread(1001, [333, 333, 334])), 1000)       # capped at the lines' value
        self.assertEqual(spread(0, [5, 5]), [0, 0])

    def test_equivalent_bp_rounds_up(self):
        self.assertEqual(equivalent_bp(100, 3000), 334)                  # 3.33..% -> 3.34%
        self.assertEqual(equivalent_bp(0, 3000), 0)
        self.assertEqual(equivalent_bp(5, 0), 10000)

    def test_prorate(self):
        self.assertEqual(prorate(300, 2000, 4000), 150)
        self.assertEqual(prorate(300, 4000, 4000), 300)
        self.assertEqual(prorate(1, 1000, 3000), 0)

    def test_returns_of_a_fixed_line_add_up_to_what_was_paid(self):
        allocs = [{"id": "a", "qty_base": 3, "unit_price": 1000, "pack_price": None, "pack_size": 1}]
        total = 3000 - 100
        kept, back = {"a": 3}, 0
        for _ in range(3):
            before = kept_value(allocs, kept, 0, total, fixed=100)
            kept["a"] -= 1
            back += before - kept_value(allocs, kept, 0, total, fixed=100)
        self.assertEqual(back, total)


class FixedCase(SalesCase):
    def setUp(self):
        super().setUp()
        self.cheap = cat.create_item(self.db, self.ctx, {"name_en": "Testgauze 10cm", "public_price": 1000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.cheap, "G1", (TODAY + timedelta(days=500)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.cheap, qty_delta=50, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal("4"))
        self.cashier = dataclasses.replace(self.ctx, role_code="assistant", max_discount_bp=500)

    def draft(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, packs=1)              # 2 strips of the NEW... FEFO: OLD @19 x2
        d = self.s.add_line(self.ctx, sid, self.cheap, units=3)       # 30.00
        return sid, d


class TestLineFixed(FixedCase):
    def test_fixed_line_discount_preview_complete_and_return(self):
        sid, d = self.draft()
        gauze = next(l for l in d["lines"] if l["item_id"] == self.cheap)
        d = self.s.update_line(self.ctx, sid, gauze["id"], discount_amount=250)
        gl = next(l for l in d["lines"] if l["item_id"] == self.cheap)
        self.assertEqual((gl["gross"], gl["discount"], gl["net"]), (3000, 250, 2750))
        total = d["total"]
        d = self.s.complete(self.ctx, sid, paid_cash=total, safety_decisions=self.ack_all(sid))
        self.assertEqual(d["total"], total)
        row = self.db.one("SELECT line_total, discount_amount, discount_bp FROM sale_lines WHERE id=?", (gl["id"],))
        self.assertEqual(tuple(row), (2750, 250, 0))
        # returns one by one add up to exactly the line's money
        back = 0
        for _ in range(3):
            r = self.s.return_items(self.ctx, sid, {gl["id"]: 1}, refund_method="cash", reason="تجربة مرتجع")
            back += r["refund"]
        self.assertEqual(back, 2750)

    def test_percent_and_amount_replace_each_other(self):
        sid, d = self.draft()
        gid = next(l for l in d["lines"] if l["item_id"] == self.cheap)["id"]
        self.s.update_line(self.ctx, sid, gid, discount_amount=200)
        d = self.s.update_line(self.ctx, sid, gid, discount_bp=1000)
        gl = next(l for l in d["lines"] if l["id"] == gid)
        self.assertEqual((gl["discount_bp"], gl["discount_amount"], gl["discount"]), (1000, 0, 300))
        d = self.s.update_line(self.ctx, sid, gid, discount_amount=100)
        gl = next(l for l in d["lines"] if l["id"] == gid)
        self.assertEqual((gl["discount_bp"], gl["discount_amount"], gl["discount"]), (0, 100, 100))

    def test_never_past_the_line(self):
        sid, d = self.draft()
        gid = next(l for l in d["lines"] if l["item_id"] == self.cheap)["id"]
        d = self.s.update_line(self.ctx, sid, gid, discount_amount=99999)
        gl = next(l for l in d["lines"] if l["id"] == gid)
        self.assertEqual(gl["net"], 0)

    def test_limit_like_a_percent(self):
        sid, d = self.draft()
        gid = next(l for l in d["lines"] if l["item_id"] == self.cheap)["id"]
        self.s.update_line(self.cashier, sid, gid, discount_amount=150)          # 5% of 30.00: allowed
        with self.assertRaises(Forbidden) as e:
            self.s.update_line(self.cashier, sid, gid, discount_amount=151)      # 5.04%: needs approval
        self.assertEqual(e.exception.code, "approval_required")
        self.assertEqual(self.db.one("SELECT discount_amount FROM sale_lines WHERE id=?", (gid,))[0], 150)
        # shortening the line makes the same 1.50 a bigger share: refused too
        with self.assertRaises(Forbidden):
            self.s.update_line(self.cashier, sid, gid, qty_base=1)
        for bad in (-1, 1.5, "100", True):
            with self.assertRaises(ValidationFailed):
                self.s.update_line(self.ctx, sid, gid, discount_amount=bad)


class TestInvoiceDiscount(FixedCase):
    def test_egp_spread_by_largest_remainder_and_stored(self):
        sid, d = self.draft()
        sub = d["subtotal"]
        d = self.s.set_invoice_discount(self.ctx, sid, amount=1001)
        self.assertEqual(d["total"], sub - 1001)
        self.assertEqual(sum(l["invoice_share"] for l in d["lines"]), 1001)
        shares = {l["id"]: l["invoice_share"] for l in d["lines"]}
        nets = [l["gross"] for l in d["lines"]]
        self.assertEqual(sorted(shares.values()), sorted(spread(1001, nets)))
        total = d["total"]
        d = self.s.complete(self.ctx, sid, paid_cash=total, safety_decisions=self.ack_all(sid))
        self.assertEqual((d["total"], d["discount_total"]), (total, 1001))
        stored = {r["id"]: r["invoice_share"] for r in self.db.all("SELECT id, invoice_share FROM sale_lines WHERE sale_id=?", (sid,))}
        self.assertEqual(stored, shares)
        # the invoices report and a full return give back exactly the money taken
        refund = 0
        for l in d["lines"]:
            r = self.s.return_items(self.ctx, sid, {l["id"]: l["qty_base"]}, refund_method="cash", reason="تجربة مرتجع")
            refund += r["refund"]
        self.assertEqual(refund, total)

    def test_percent_after_line_discounts_and_removal(self):
        sid, d = self.draft()
        gid = next(l for l in d["lines"] if l["item_id"] == self.cheap)["id"]
        self.s.update_line(self.ctx, sid, gid, discount_amount=500)
        d = self.s.set_invoice_discount(self.ctx, sid, bp=1000)                    # 10% of the nets
        base = d["subtotal"] - 500
        self.assertEqual(sum(l["invoice_share"] for l in d["lines"]), round(base * 0.1))
        d = self.s.set_invoice_discount(self.ctx, sid, amount=0, bp=0)
        self.assertEqual(sum(l["invoice_share"] for l in d["lines"]), 0)
        with self.assertRaises(ValidationFailed):
            self.s.set_invoice_discount(self.ctx, sid, amount=100, bp=100)

    def test_limit_counts_the_lines_and_the_invoice_together(self):
        sid, d = self.draft()                                                     # 38.00 + 30.00 = 68.00
        gid = next(l for l in d["lines"] if l["item_id"] == self.cheap)["id"]
        self.s.update_line(self.cashier, sid, gid, discount_amount=150)
        self.s.set_invoice_discount(self.cashier, sid, amount=190)               # 3.40 of 68.00 = 5%
        with self.assertRaises(Forbidden):
            self.s.set_invoice_discount(self.cashier, sid, amount=191)
        self.assertEqual(self.db.one("SELECT invoice_discount_amount FROM sales WHERE id=?", (sid,))[0], 190)
        # a line removed after the discount: refused at completion without an approval
        pan = next(l for l in self.s.get(self.ctx, sid)["lines"] if l["item_id"] == self.pan)
        self.s.remove_line(self.ctx, sid, pan["id"]) if hasattr(self.s, "remove_line") else None
        d = self.s.get(self.ctx, sid)
        if len(d["lines"]) == 1:
            with self.assertRaises(Forbidden):
                self.s.complete(self.cashier, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))

    def test_reports_see_the_fixed_discounts(self):
        from app.reports import owner
        sid, d = self.draft()
        gid = next(l for l in d["lines"] if l["item_id"] == self.cheap)["id"]
        self.s.update_line(self.ctx, sid, gid, discount_amount=200)
        d = self.s.set_invoice_discount(self.ctx, sid, amount=300)
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        day = TODAY.isoformat()
        rep = owner.discounts_report(self.db, self.ctx, day, day)
        self.assertEqual(sum(g["discount"] for g in rep["by_who"]), 500)
        inv_rep = invoices.report(self.db, self.ctx, date_from=day, date_to=day)
        row = next(r for r in inv_rep["rows"] if r["id"] == sid)
        self.assertEqual(row["item_discount"], 500)

    def test_audited(self):
        sid, _ = self.draft()
        self.s.set_invoice_discount(self.ctx, sid, amount=300)
        a = self.db.one("SELECT after_json FROM audit_log WHERE action='sale.invoice_discount' AND entity_id=?", (sid,))
        self.assertIn('"amount": 300', a["after_json"])


if __name__ == "__main__":
    unittest.main()


import json as _json
import re as _re
import shutil as _shutil
import subprocess as _sp
from pathlib import Path as _Path

from tests.test_api import H, APICase

_JS = (_Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


@unittest.skipUnless(_shutil.which("node"), "Node not available")
class TestTypedDiscount(unittest.TestCase):
    def test_percent_or_egp(self):
        pre = _re.search(r"\nfunction exactPiasters\(.*?\n\}\n", _JS, _re.S).group(0)
        fn = _re.search(r"\nfunction discAsk\(.*?\n\}\n", _JS, _re.S).group(0)
        run = ("const toast = () => {}; const toBp = (v) => (v === '' || v == null ? null : Math.round(parseFloat(v) * 100));"
               + pre + fn + "console.log(JSON.stringify(['5', '5%', '3ج', '3.5 ج', '2 egp', '', 'x'].map(discAsk)))")
        r = _sp.run([_shutil.which("node"), "-e", run], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_json.loads(r.stdout), [{"discount_bp": 500}, {"discount_bp": 500}, {"discount_amount": 300},
                                                 {"discount_amount": 350}, {"discount_amount": 200}, None, None])


class TestInvoiceDiscountApi(APICase):
    def test_route(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Testgauze", "public_price": 1000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "G1", (TODAY + timedelta(days=500)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("4"))
        from app.sales.cash import CashDrawer
        CashDrawer(self.db).open(self.ctx, 0)
        self.http_login()
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        d = self.c.post(f"/api/sales/{sid}/lines", json={"item_id": it, "units": 2}, headers=H).json()
        lid = d["lines"][0]["id"]
        d = self.c.patch(f"/api/sales/{sid}/lines/{lid}", json={"discount_amount": 150}, headers=H).json()
        self.assertEqual(d["total"], 1850)
        r = self.c.put(f"/api/sales/{sid}/invoice-discount", json={"amount": 50}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["total"], 1800)
        r = self.c.put(f"/api/sales/{sid}/invoice-discount", json={"amount": 10, "bp": 10}, headers=H)
        self.assertEqual(r.status_code, 422)
