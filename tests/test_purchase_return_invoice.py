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

    def test_credit_is_the_approved_line_amount_not_the_rounded_unit_price(self):
        """Codex P1 on #396: 3 packs at a rounded 30.00 but the line charged 89.99: a full return credits 89.99."""
        item = cat.create_item(self.db, self.ctx, {"name_en": "Rounded tab", "pack_size": 1, "public_price": 3333})
        inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.update_header(self.ctx, inv, {"invoice_no": "F-78", "invoice_date": date.today().isoformat()}, v)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.add_line(self.ctx, inv, {"item_id": item, "qty": 3, "bonus_qty": 0, "unit_net_price": 3000,
                                        "line_total": 8999, "batch_no": "R1", "expiry_date": FUT}, v)
        self.p.approve(self.ctx, inv, self.p.get(self.ctx, inv)["invoice"]["version"])
        bal = self.balance()
        line = returns.from_invoice(self.db, self.ctx, self.sup, "F-78")["lines"][0]
        r = returns.create_from_invoice(self.db, self.ctx, self.sup, inv,
                                        [{"purchase_line_id": line["id"], "paid_units": 3, "reason": "expired"}])
        self.assertEqual(r["total"], 8999)
        self.assertEqual(self.balance() - bal, -8999)

    def test_the_slip_shows_the_tax_and_adds_up(self):
        """Codex P2 on #396: the signed slip shows the tax column, the credit note and lines that add up."""
        from starlette.testclient import TestClient
        from app.api.app import create_app
        from tests.helpers import OWNER_PW
        st = replace(config.load(), upload_dir=self.tmp / "up2", anthropic_api_key=None, data_dir=self.tmp,
                     backup_dir=self.tmp / "backups")
        c = TestClient(create_app(self.db, st, Purchasing(self.db, st)))
        H = {"X-Requested-With": "pharm"}
        c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H)
        r = self.ret(self.tax, 1, credit_note_no="CN-9")
        page = c.get(f"/supplier-return/{r['id']}", headers=H).text
        self.assertIn("الضريبة", page)
        self.assertIn("CN-9", page)
        self.assertIn("45.60", page)
        self.assertIn("5.60", page)


class TestCancelAfterInvoiceReturn(Base):
    """Codex P1 x2 on #396: cancelling an invoice after a return from it credits nothing twice and takes no
    other invoice's units."""

    def invoice(self, no, qty):
        inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.update_header(self.ctx, inv, {"invoice_no": no, "invoice_date": date.today().isoformat()}, v)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.add_line(self.ctx, inv, {"item_id": self.tax, "qty": qty, "bonus_qty": 0, "unit_net_price": 4000,
                                        "line_total": 4000 * qty, "batch_no": "T1", "expiry_date": FUT}, v)
        self.p.approve(self.ctx, inv, self.p.get(self.ctx, inv)["invoice"]["version"])
        return inv

    def cancel(self, inv):
        self.p.cancel(self.ctx, inv, "إلغاء تجربة", self.p.get(self.ctx, inv)["invoice"]["version"])

    def qty(self):
        return self.db.one("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE item_id=?", (self.tax,))["q"]

    def test_tax_already_credited_is_not_credited_again(self):
        bal = self.balance()
        self.ret(self.tax, 1)                                   # 40.00 + 5.60 tax
        self.cancel(self.inv)
        posted = self.db.one("SELECT coalesce(sum(amount),0) a FROM supplier_ledger WHERE ref_id=? AND kind='purchase'",
                             (self.inv,))["a"]
        moved = self.db.one("SELECT coalesce(sum(amount),0) a FROM supplier_ledger WHERE supplier_id=? AND"
                            " (ref_id=? OR ref_id IN (SELECT id FROM supplier_returns WHERE purchase_invoice_id=?))",
                            (self.sup, self.inv, self.inv))["a"]
        self.assertEqual((posted > 0, moved), (True, 0))        # invoice + its return + its cancellation = 0

    def test_a_linked_return_is_taken_from_its_own_invoice(self):
        second = self.invoice("F-79", 3)
        before = self.qty()
        line = returns.from_invoice(self.db, self.ctx, self.sup, "F-79")["lines"][0]
        returns.create_from_invoice(self.db, self.ctx, self.sup, second,
                                    [{"purchase_line_id": line["id"], "paid_units": 3, "reason": "expired"}])
        bal = self.balance()
        self.cancel(second)                                     # everything of it already went back
        self.assertEqual(self.qty(), before - 3)                # the first invoice's 3 units stay
        self.assertEqual(self.balance(), bal)                   # and nothing is credited again


class TestCodexReview397(Base):
    def invoice(self, no, qty, net, *, extra_discount=0, extra_tax=0):
        inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.update_header(self.ctx, inv, {"invoice_no": no, "invoice_date": date.today().isoformat(),
                                             "extra_discount": extra_discount, "extra_tax": extra_tax}, v)
        v = self.p.get(self.ctx, inv)["invoice"]["version"]
        self.p.add_line(self.ctx, inv, {"item_id": self.tax, "qty": qty, "bonus_qty": 0, "unit_net_price": net,
                                        "line_total": net * qty, "batch_no": "T1", "expiry_date": FUT}, v)
        self.p.approve(self.ctx, inv, self.p.get(self.ctx, inv)["invoice"]["version"])
        return inv

    def full_return(self, inv, no):
        line = returns.from_invoice(self.db, self.ctx, self.sup, no)["lines"][0]
        return returns.create_from_invoice(self.db, self.ctx, self.sup, inv,
                                           [{"purchase_line_id": line["id"], "paid_units": line["paid_units"]
                                             - line["paid_returned"], "reason": "expired"}])

    def test_the_invoice_discount_and_tax_are_part_of_the_credit(self):
        for no, d, t, want in (("D-1", 1000, 0, 11000), ("D-2", 0, 700, 12700)):
            bal = self.balance()
            inv = self.invoice(no, 3, 4000, extra_discount=d, extra_tax=t)
            self.assertEqual(self.balance() - bal, want)                   # what approval charged
            self.assertEqual(self.full_return(inv, no)["total"], want)      # comes back exactly
            self.assertEqual(self.balance(), bal)

    def test_units_returned_earlier_by_lot_are_not_returned_again_from_the_invoice(self):
        batch = self.lines[self.tax]["batch_id"]
        returns.create(self.db, self.ctx, self.sup, [{"item_id": self.tax, "qty_base": 3, "reason": "damaged",
                                                      "batch_id": batch}])
        self.invoice("R-2", 3, 3000)                                         # the same lot comes in again
        line = returns.from_invoice(self.db, self.ctx, self.sup, "F-77")["lines"][1]
        self.assertEqual((line["paid_returned"], line["receipt_left"]), (3, 0))
        with self.assertRaises(Conflict):
            returns.create_from_invoice(self.db, self.ctx, self.sup, self.inv,
                                        [{"purchase_line_id": line["id"], "paid_units": 3, "reason": "expired"}])


class TestIndependentReview(Base):
    def test_a_return_from_an_invoice_is_made_in_its_own_branch(self):
        other = replace(self.ctx, branch_id="another-branch")
        with self.assertRaises(Conflict) as e:
            returns.create_from_invoice(self.db, other, self.sup, self.inv, [{"purchase_line_id": self.lines[self.tax]["id"],
                                        "paid_units": 1, "reason": "expired"}])
        self.assertEqual(e.exception.code, "other_branch")
