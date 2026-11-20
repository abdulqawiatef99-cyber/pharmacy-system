"""Part 3 item 6b: a purchase return from its invoice. Supplier + invoice number load the lines; paid units
credited at the invoice's net price plus their tax share, bonus units back on their own line at 0, the
supplier's credit-note number kept, default settlement on the supplier's account, and the owner's rule #310:
a line that came with bonus units goes back whole unless the person may return part of it and writes why."""
from dataclasses import replace
from datetime import date, timedelta

from app import config
from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.procurement import returns
from app.purchasing.service import Purchasing
from tests.helpers import DBTestCase

FUT = (date.today() + timedelta(days=400)).isoformat()


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد المرتجع")
        self.bon = cat.create_item(self.db, self.ctx, {"name_en": "Bonus tab", "pack_size": 2, "public_price": 4000})
        self.tax = cat.create_item(self.db, self.ctx, {"name_en": "Taxed cream", "pack_size": 1, "public_price": 5000})
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        d = self.p.update_header(self.ctx, inv, {"invoice_no": "F-77", "invoice_date": date.today().isoformat()}, v)
        # 10 packs + 2 bonus at 30.00 net = 300.00; 3 packs at 40.00 net + 16.80 tax = 136.80
        for data in ({"item_id": self.bon, "qty": 10, "bonus_qty": 2, "unit_net_price": 3000, "line_total": 30000,
                      "batch_no": "B1", "expiry_date": FUT},
                     {"item_id": self.tax, "qty": 3, "bonus_qty": 0, "unit_net_price": 4000, "tax_amount": 1680,
                      "line_total": 13680, "batch_no": "T1", "expiry_date": FUT}):
            v = self.p.get(self.ctx, inv)["invoice"]["version"]
            self.p.add_line(self.ctx, inv, data, v)
        d = self.p.get(self.ctx, inv)
        self.p.approve(self.ctx, inv, d["invoice"]["version"])
        self.inv = inv
        loaded = returns.from_invoice(self.db, self.ctx, self.sup, "F-77")
        self.lines = {l["item_id"]: l for l in loaded["lines"]}

    def balance(self):
        return self.db.one("SELECT coalesce(sum(amount),0) b FROM supplier_ledger WHERE supplier_id=?", (self.sup,))["b"]

    def ret(self, item, paid, bonus=0, **kw):
        return returns.create_from_invoice(self.db, self.ctx, self.sup, self.inv,
                                           [{"purchase_line_id": self.lines[item]["id"], "paid_units": paid,
                                             "bonus_units": bonus, "reason": "expired"}], **kw)


class TestFromInvoice(Base):
    def test_load_shows_paid_and_bonus_units(self):
        b, t = self.lines[self.bon], self.lines[self.tax]
        self.assertEqual((b["paid_units"], b["bonus_units"], b["paid_returned"]), (20, 4, 0))
        self.assertEqual((t["paid_units"], t["bonus_units"], t["tax_amount"]), (3, 0, 1680))
        self.assertEqual(b["on_hand"], 24)
        with self.assertRaises(NotFound):
            returns.from_invoice(self.db, self.ctx, self.sup, "NOPE")

    def test_bonus_line_returned_whole_paid_at_net_bonus_at_zero(self):
        bal = self.balance()
        r = self.ret(self.bon, 20, 4, credit_note_no="CN-5")
        self.assertEqual((r["total"], r["credit_note_no"], r["tax_total"]), (30000, "CN-5", 0))
        rows = sorted((l["is_bonus"], l["qty_base"], l["unit_value"]) for l in r["lines"])
        self.assertEqual(rows, [(0, 20, 1500), (1, 4, 0)])
        self.assertEqual(self.balance() - bal, -30000)                      # on the supplier's account
        self.assertEqual(returns.from_invoice(self.db, self.ctx, self.sup, "F-77")["lines"][0]["on_hand"], 0)

    def test_part_of_a_bonus_line_is_refused_then_allowed_with_the_right_and_a_reason(self):
        uid = people.add_user(self.db, self.ctx, username="mgr1", full_name="م", password="Staff-Pass-2026",
                              role_code="manager")
        mgr = self.login("mgr1", "Staff-Pass-2026")
        with self.assertRaises(Forbidden):
            returns.create_from_invoice(self.db, mgr, self.sup, self.inv, [{"purchase_line_id": self.lines[self.bon]["id"],
                                        "paid_units": 10, "bonus_units": 0, "reason": "expired"}])
        with self.assertRaises(ValidationFailed) as e:          # the owner has the right, but must say why
            self.ret(self.bon, 10, 0)
        self.assertEqual(e.exception.code, "bonus_line_whole")
        r = self.ret(self.bon, 10, 2, partial_bonus_reason="المورد قبل نص الكمية")
        self.assertEqual(r["total"], 15000)
        self.assertIn("المورد قبل نص الكمية", r["note"])
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_returns WHERE created_by=?", (uid,)))

    def test_tax_is_credited_and_partial_returns_add_up_exactly(self):
        bal = self.balance()
        a = self.ret(self.tax, 1)
        b = self.ret(self.tax, 1)
        c = self.ret(self.tax, 1)
        self.assertEqual(a["total"] + b["total"] + c["total"], 13680)
        self.assertEqual(a["tax_total"] + b["tax_total"] + c["tax_total"], 1680)
        self.assertEqual(self.balance() - bal, -13680)
        with self.assertRaises(Conflict):
            self.ret(self.tax, 1)

    def test_never_more_than_left_or_on_hand_and_nothing_written_on_refusal(self):
        with self.assertRaises(Conflict):
            self.ret(self.tax, 4)
        with self.assertRaises(ValidationFailed):
            self.ret(self.tax, 0)
        with self.assertRaises(ValidationFailed):
            returns.create_from_invoice(self.db, self.ctx, self.sup, self.inv, [{"purchase_line_id": self.lines[self.tax]["id"],
                                        "paid_units": 1, "reason": "bad"}])
        other = cat.create_supplier(self.db, self.ctx, name="مورد تاني")
        with self.assertRaises(NotFound):
            returns.create_from_invoice(self.db, self.ctx, other, self.inv, [{"purchase_line_id": self.lines[self.tax]["id"],
                                        "paid_units": 1, "reason": "expired"}])
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_returns"))

    def test_old_by_item_returns_still_work(self):
        r = returns.create(self.db, self.ctx, self.sup, [{"item_id": self.tax, "qty_base": 1, "reason": "damaged",
                                                        "batch_id": self.lines[self.tax]["batch_id"]}])
        self.assertIsNone(r["purchase_invoice_id"])


class TestCodexReview396(Base):
    def test_the_same_line_twice_is_refused(self):
        lid = self.lines[self.tax]["id"]
        with self.assertRaises(ValidationFailed):
            returns.create_from_invoice(self.db, self.ctx, self.sup, self.inv,
                                        [{"purchase_line_id": lid, "paid_units": 2, "reason": "expired"},
                                         {"purchase_line_id": lid, "paid_units": 2, "reason": "expired"}])
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_returns"))
