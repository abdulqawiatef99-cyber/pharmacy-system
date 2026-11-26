"""#324 group D, the P1s fixed before launch (19/11): S5/X11 (a manual purchase and another pharmacy's
supplier), S19 (the old supplier's code mapping after a correction), S28 (cancelling an approved purchase),
S29-S31 (returns to a supplier) and S32 (the loss report). The first reproductions lived in
tests/test_review_324_after_launch.py as expectedFailure; they moved here with the fixes. Synthetic data only."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, DomainError
from app.inventory import adjust, service as inv
from app.procurement import returns, suppliers as sup
from app.purchasing.model import ExtractedInvoice, ExtractedLine
from app.purchasing.service import Purchasing
from app.sales.service import local_today
from tests.helpers import DBTestCase

T = date.today()


def _batch(db, ctx, item, qty, batch="B1", cost="15", days=400, mtype="purchase"):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, batch, (T + timedelta(days=days)).isoformat())
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type=mtype,
                          unit_cost=Decimal(cost))
    return b


class _Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db)
        self._no = 0

    def item(self, name="Tab", pack=1, price=1000, **kw):
        return cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": pack, "public_price": price, **kw})

    def supplier(self, name):
        return cat.create_supplier(self.db, self.ctx, name=name)

    def buy(self, supplier, item, qty, net, lot="L1", expiry="12/2029", total=None, extra_discount=None):
        """An approved purchase invoice: `qty` packs at `net` piasters each. Returns (invoice id, version)."""
        self._no += 1
        iid = self.p.create_manual(self.ctx, supplier_id=supplier)
        v = self.p.get(self.ctx, iid)["invoice"]["version"]
        line = {"item_id": item, "qty": qty, "unit_net_price": net}
        if lot:
            line |= {"batch_no": lot, "expiry_date": expiry}
        v = self.p.add_line(self.ctx, iid, line, v)["invoice"]["version"]
        head = {"invoice_no": f"INV-{self._no}", "invoice_date": local_today().isoformat(),
                "declared_total": total if total is not None else qty * net}
        if extra_discount:
            head["extra_discount"] = extra_discount
        v = self.p.update_header(self.ctx, iid, head, v)["invoice"]["version"]
        d = self.p.approve(self.ctx, iid, v)
        self.assertEqual(d["invoice"]["status"], "approved", d["issues"])
        return iid, d["invoice"]["version"]

    def lot(self, item, lot="L1"):
        return self.db.one("SELECT id FROM batches WHERE item_id=? AND batch_no=?", (item, lot))["id"]

    def qty(self, item, batch=None):
        if batch is None:
            return inv.stock_for_item(self.db, self.ctx, item)["total_qty"]
        return inv.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, item, batch)

    def owed(self, supplier):
        return sup.balance(self.db, self.ctx.org_id, supplier)


# ---------------------------------------------------------------- S5 / X11
class TestS5X11ForeignSupplier(_Base):
    def test_a_manual_invoice_cannot_use_another_pharmacys_supplier(self):
        _, other = self.second_tenant()
        foreign = cat.create_supplier(self.db, other, name="مورد صيدلية تانية")
        with self.assertRaises(DomainError):
            self.p.create_manual(self.ctx, supplier_id=foreign)
        self.assertEqual(self.db.one("SELECT count(*) n FROM purchase_invoices")["n"], 0)

    def test_an_extraction_cannot_be_linked_to_another_pharmacys_supplier(self):
        _, other = self.second_tenant()
        foreign = cat.create_supplier(self.db, other, name="مورد صيدلية تانية")
        ex = ExtractedInvoice(method="structured", lines=[ExtractedLine(raw_name="x", qty=1)])
        with self.assertRaises(DomainError):
            self.p._create_from_extraction(self.ctx, ex, "excel", None, foreign)

    def test_a_draft_already_linked_to_a_foreign_supplier_is_not_named_or_approved(self):
        """Second line of defence for a row that got in before the check: no name leaks, no liability posts."""
        _, other = self.second_tenant()
        foreign = cat.create_supplier(self.db, other, name="مورد صيدلية تانية")
        it = self.item("Own Tab")
        iid = self.p.create_manual(self.ctx)
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoices SET supplier_id=? WHERE id=?", (foreign, iid))
        d = self.p.get(self.ctx, iid)
        self.assertIsNone(d["invoice"].get("supplier_name"))
        v = self.p.add_line(self.ctx, iid, {"item_id": it, "qty": 1, "unit_net_price": 2000, "batch_no": "L1",
                                            "expiry_date": "12/2029"}, d["invoice"]["version"])["invoice"]["version"]
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoices SET invoice_no='9', invoice_date=?, declared_total=2000 WHERE id=?",
                            (local_today().isoformat(), iid))
        with self.assertRaises(DomainError):
            self.p.approve(self.ctx, iid, v)
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_ledger")["n"], 0)
        self.assertEqual(self.qty(it), 0)


# ---------------------------------------------------------------- S19
class TestS19SupplierChangeRematches(_Base):
    def setUp(self):
        super().setUp()
        self.i100, self.i200 = self.item("Zeta 100mg"), self.item("Omega 200mg")
        self.a, self.b = self.supplier("مورد أ"), self.supplier("مورد ب")
        with self.db.transaction():
            for s_, i_ in ((self.a, self.i100), (self.b, self.i200)):
                self.db.execute("INSERT INTO supplier_item_codes (org_id, supplier_id, code, item_id) VALUES (?,?,?,?)",
                                (self.ctx.org_id, s_, "SKU9", i_))

    def draft(self, supplier, lines):
        ex = ExtractedInvoice(method="structured", lines=lines)
        return self.p._create_from_extraction(self.ctx, ex, "excel", None, supplier)

    def test_changing_the_supplier_rematches_the_lines(self):
        iid = self.draft(self.a, [ExtractedLine(raw_name="unknown name", raw_code="SKU9", qty=1)])
        self.assertEqual(self.p.get(self.ctx, iid)["lines"][0]["item_id"], self.i100)
        d = self.p.update_header(self.ctx, iid, {"supplier_id": self.b}, self.p.get(self.ctx, iid)["invoice"]["version"])
        self.assertEqual(d["lines"][0]["item_id"], self.i200)
        self.assertEqual(d["lines"][0]["match_method"], "supplier_code")

    def test_a_supplier_without_that_code_leaves_the_line_unmatched(self):
        c = self.supplier("مورد ج")
        iid = self.draft(self.a, [ExtractedLine(raw_name="unknown name", raw_code="SKU9", qty=1)])
        d = self.p.update_header(self.ctx, iid, {"supplier_id": c}, self.p.get(self.ctx, iid)["invoice"]["version"])
        self.assertIsNone(d["lines"][0]["item_id"])
        self.assertEqual(d["lines"][0]["match_status"], "unmatched")

    def test_a_line_a_person_confirmed_keeps_its_item(self):
        iid = self.draft(self.a, [ExtractedLine(raw_name="unknown name", raw_code="SKU9", qty=1)])
        d = self.p.get(self.ctx, iid)
        d = self.p.update_line(self.ctx, iid, d["lines"][0]["id"], {"item_id": self.i100}, d["invoice"]["version"])
        d = self.p.update_header(self.ctx, iid, {"supplier_id": self.b}, d["invoice"]["version"])
        self.assertEqual(d["lines"][0]["item_id"], self.i100)
        self.assertEqual(d["lines"][0]["match_status"], "confirmed")

    def test_other_header_edits_do_not_rematch(self):
        iid = self.draft(self.a, [ExtractedLine(raw_name="unknown name", raw_code="SKU9", qty=1)])
        with self.db.transaction():      # the mapping changed after the import: an unrelated edit must not move the line
            self.db.execute("UPDATE supplier_item_codes SET item_id=? WHERE supplier_id=?", (self.i200, self.a))
        d = self.p.update_header(self.ctx, iid, {"notes": "x", "supplier_id": self.a},
                                 self.p.get(self.ctx, iid)["invoice"]["version"])
        self.assertEqual(d["lines"][0]["item_id"], self.i100)

    def test_the_rematch_is_in_the_audit_log(self):
        iid = self.draft(self.a, [ExtractedLine(raw_name="unknown name", raw_code="SKU9", qty=1)])
        self.p.update_header(self.ctx, iid, {"supplier_id": self.b}, self.p.get(self.ctx, iid)["invoice"]["version"])
        row = self.db.one("SELECT after_json FROM audit_log WHERE action='purchase.header_edit' ORDER BY rowid DESC LIMIT 1")
        self.assertIn("rematched", row["after_json"])


# ---------------------------------------------------------------- S28
class TestS28CancelApprovedPurchase(_Base):
    def setUp(self):
        super().setUp()
        self.it = self.item("Cancel Tab")
        self.a, self.b = self.supplier("أ"), self.supplier("ب")

    def test_plain_cancel_reverses_everything(self):
        iid, v = self.buy(self.a, self.it, 10, 100)
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)

    def test_codex_scenario_a_later_receipt_and_an_earlier_credit_stay(self):
        iid, v = self.buy(self.a, self.it, 10, 100)
        lot = self.lot(self.it)
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": lot, "qty_base": 4, "reason": "expired"}])
        self.assertEqual(self.owed(self.a), 600)
        self.buy(self.b, self.it, 4, 100)                    # supplier B delivers four units of the same lot
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 4)               # B's four stay
        self.assertEqual(self.owed(self.a), 0)               # 1000 - 400 (return) - 600 (cancel), never -400
        self.assertEqual(self.owed(self.b), 400)
        rev = self.db.one("SELECT sum(qty_delta) q FROM stock_movements WHERE movement_type='purchase_reversal'")["q"]
        self.assertEqual(rev, -6)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_a_return_credited_at_another_value_still_nets_the_invoice_to_zero(self):
        iid, v = self.buy(self.a, self.it, 10, 100)
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.lot(self.it), "qty_base": 4,
                                                    "reason": "expired", "unit_value": 90}])
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)

    def test_a_free_invoice_with_a_credited_return_still_comes_to_zero(self):
        """Codex P2 on #347: nothing was posted for the invoice, but a return of its units was credited at an
        agreed value; the cancel must take that credit back or the supplier is left owing 3.60."""
        iid, v = self.buy(self.a, self.it, 10, 100, total=0, extra_discount=1000)
        self.assertEqual(self.owed(self.a), 0)
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.lot(self.it), "qty_base": 4,
                                                    "reason": "expired", "unit_value": 90}])
        self.assertEqual(self.owed(self.a), -360)
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)

    def test_units_sold_are_not_covered_by_another_suppliers_later_receipt(self):
        """Before: 10 from A, 3 leave, B delivers 4 of the lot; cancelling A took 10 of the 11 and left B one."""
        iid, v = self.buy(self.a, self.it, 10, 100)
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.it, qty_delta=-3, batch_id=self.lot(self.it),
                              movement_type="sale")
        self.buy(self.b, self.it, 4, 100)
        with self.assertRaises(Conflict) as c:
            self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(c.exception.code, "insufficient_stock")
        self.assertEqual(self.qty(self.it), 11)
        self.assertEqual(self.owed(self.a), 1000)
        self.assertEqual(self.p.get(self.ctx, iid)["invoice"]["status"], "approved")

    def test_an_earlier_receipt_covers_units_sold_first_in_first_out(self):
        self.buy(self.b, self.it, 5, 100)
        iid, v = self.buy(self.a, self.it, 10, 100)
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.it, qty_delta=-3, batch_id=self.lot(self.it),
                              movement_type="sale")
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 2)
        self.assertEqual(self.owed(self.a), 0)

    def test_a_return_to_another_supplier_is_not_taken_off_this_invoice(self):
        iid, v = self.buy(self.a, self.it, 10, 100)
        self.buy(self.b, self.it, 4, 100)
        returns.create(self.db, self.ctx, self.b, [{"item_id": self.it, "batch_id": self.lot(self.it), "qty_base": 4,
                                                    "reason": "expired"}])
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)
        self.assertEqual(self.owed(self.b), 0)

    def test_two_invoices_of_one_supplier_and_lot_absorb_a_return_once(self):
        i1, v1 = self.buy(self.a, self.it, 10, 100)
        i2, v2 = self.buy(self.a, self.it, 10, 100)
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.lot(self.it), "qty_base": 4,
                                                    "reason": "expired"}])
        self.p.cancel(self.ctx, i1, "إلغاء تجربة", v1)       # the oldest receipt carries the return
        self.assertEqual(self.qty(self.it), 10)
        self.assertEqual(self.owed(self.a), 1000)
        self.p.cancel(self.ctx, i2, "إلغاء تجربة", v2)       # the second one reverses whole: nothing left to absorb
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)

    def test_untracked_stock_follows_the_same_rule(self):
        iid, v = self.buy(self.a, self.it, 10, 100, lot=None)
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "qty_base": 4, "reason": "damaged"}])
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)

    def test_everything_returned_then_cancelled_moves_no_stock(self):
        iid, v = self.buy(self.a, self.it, 10, 100)
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.lot(self.it), "qty_base": 10,
                                                    "reason": "wrong_item"}])
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(self.qty(self.it), 0)
        self.assertEqual(self.owed(self.a), 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM stock_movements WHERE movement_type='purchase_reversal'")["n"], 0)


# ---------------------------------------------------------------- S29
class TestS29UntrackedReturn(_Base):
    def setUp(self):
        super().setUp()
        self.it, self.a = self.item("Mixed Tab"), self.supplier("أ")
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.it, qty_delta=5, batch_id=None, movement_type="purchase",
                              unit_cost=Decimal("1"))
        self.tracked = _batch(self.db, self.ctx, self.it, 3, batch="T1", days=100)

    def test_returning_untracked_units_takes_untracked_stock(self):
        returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "qty_base": 2, "reason": "expired", "unit_value": 100}])
        self.assertEqual(self.qty(self.it, self.tracked), 3)
        self.assertEqual(inv.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, self.it, None), 3)

    def test_more_than_the_untracked_stock_is_refused_not_taken_from_a_lot(self):
        with self.assertRaises(Conflict) as c:
            returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "qty_base": 6, "reason": "expired", "unit_value": 100}])
        self.assertEqual(c.exception.code, "insufficient_stock")
        self.assertEqual(self.qty(self.it), 8)
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_returns")["n"], 0)


# ---------------------------------------------------------------- S30
class TestS30ReturnPriceOfThisSupplier(_Base):
    def setUp(self):
        super().setUp()
        self.it = self.item("Lot Tab")
        self.a, self.b = self.supplier("أ"), self.supplier("ب")
        self.buy(self.a, self.it, 5, 1200)
        self.buy(self.b, self.it, 5, 1800)                   # same lot, later, other supplier
        self.lot_id = self.lot(self.it)

    def test_the_suggested_value_is_what_this_supplier_was_paid(self):
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, self.lot_id, self.a), 1200)
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, self.lot_id, self.b), 1800)

    def test_without_a_supplier_the_lowest_price_paid_for_the_lot_is_suggested(self):
        """The screen asks before a supplier is known: never propose more than a supplier was paid."""
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, self.lot_id), 1200)

    def test_a_return_without_a_typed_value_credits_this_suppliers_price(self):
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.lot_id, "qty_base": 2,
                                                        "reason": "expired"}])
        self.assertEqual(r["total"], 2400)                   # was 3600: B's later price
        self.assertEqual(self.owed(self.a), 5 * 1200 - 2400)

    def test_the_screens_blind_proposal_sent_back_is_saved_at_this_suppliers_price(self):
        """The screen asks for the proposal before it knows the supplier and posts it back as the value
        (Codex on #347): untouched, it means "at the purchase price", and that is this supplier's own."""
        blind = returns.suggest_value(self.db, self.ctx, self.it, self.lot_id)
        for supplier, paid in ((self.a, 1200), (self.b, 1800)):
            r = returns.create(self.db, self.ctx, supplier, [{"item_id": self.it, "batch_id": self.lot_id, "qty_base": 2,
                                                              "reason": "expired", "unit_value": blind}])
            self.assertEqual(r["total"], 2 * paid)

    def test_a_value_that_is_neither_proposal_is_kept_as_typed(self):
        r = returns.create(self.db, self.ctx, self.b, [{"item_id": self.it, "batch_id": self.lot_id, "qty_base": 2,
                                                        "reason": "expired", "unit_value": 1500}])
        self.assertEqual(r["total"], 3000)

    def test_a_supplier_who_never_supplied_the_lot_gets_the_lots_lowest_price(self):
        c = self.supplier("ج")
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, self.lot_id, c), 1200)

    def test_a_cancelled_invoice_is_not_a_price(self):
        it = self.item("Other Tab")
        iid, v = self.buy(self.a, it, 5, 700, lot="K1")
        lot = self.lot(it, "K1")
        self.buy(self.b, it, 5, 900, lot="K1")
        self.p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(returns.suggest_value(self.db, self.ctx, it, lot, self.a), 900)

    def test_another_pharmacys_supplier_changes_nothing(self):
        _, other = self.second_tenant()
        foreign = cat.create_supplier(self.db, other, name="مورد صيدلية تانية")
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, self.lot_id, foreign), 1200)

    def test_opening_stock_keeps_its_own_cost(self):
        it = self.item("Opening Tab")
        b = _batch(self.db, self.ctx, it, 5, batch="O1", cost="7.5", mtype="opening")
        self.assertEqual(returns.suggest_value(self.db, self.ctx, it, b, self.a), 750)


# ---------------------------------------------------------------- S31
class TestS31RoundOnceAfterMultiplying(_Base):
    def setUp(self):
        super().setUp()
        self.it, self.a = self.item("Pack3", pack=3, price=300), self.supplier("أ")
        self.b = _batch(self.db, self.ctx, self.it, 6, cost=str(Decimal(1) / 3))   # 100 piasters a pack of three

    def test_a_whole_pack_is_credited_what_it_cost(self):
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 3, "reason": "expired"}])
        self.assertEqual(r["total"], 100)
        self.assertEqual(self.owed(self.a), -100)

    def test_the_slip_lines_add_up_to_the_total(self):
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 3, "reason": "expired"}])
        self.assertEqual(sum(x["qty_base"] * x["unit_value"] for x in r["lines"]), r["total"])
        self.assertEqual(sum(x["qty_base"] for x in r["lines"]), 3)
        self.assertEqual(self.qty(self.it, self.b), 3)

    def test_the_suggested_value_sent_back_by_the_screen_means_the_purchase_price(self):
        """The screen posts the rounded suggestion (33) as the value: still one pack = 100, not 99."""
        v = returns.suggest_value(self.db, self.ctx, self.it, self.b, self.a)
        self.assertEqual(v, 33)
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 3,
                                                        "reason": "expired", "unit_value": v}])
        self.assertEqual(r["total"], 100)

    def test_a_value_the_person_typed_is_used_as_typed(self):
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 3,
                                                        "reason": "expired", "unit_value": 30}])
        self.assertEqual(r["total"], 90)
        self.assertEqual(len(r["lines"]), 1)

    def test_a_cost_in_whole_piasters_stays_one_line(self):
        it = self.item("Whole")
        b = _batch(self.db, self.ctx, it, 5, cost="12")
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": it, "batch_id": b, "qty_base": 2, "reason": "expired"}])
        self.assertEqual((r["total"], len(r["lines"])), (2400, 1))

    def test_one_unit_of_three_is_a_third_rounded_once(self):
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 1, "reason": "expired"}])
        self.assertEqual(r["total"], 33)
        r = returns.create(self.db, self.ctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 2, "reason": "expired"}])
        self.assertEqual(r["total"], 67)

    def test_staff_who_may_not_see_values_get_no_value_from_the_answer(self):
        uid = people.add_user(self.db, self.ctx, username="store", full_name="x", password="Staff-Pass-2026",
                              role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "purchases.approve", "grant")
        sctx = self.login("store", "Staff-Pass-2026")
        r = returns.create(self.db, sctx, self.a, [{"item_id": self.it, "batch_id": self.b, "qty_base": 3, "reason": "expired"}])
        self.assertNotIn("total", r)
        self.assertEqual(self.owed(self.a), -100)


# ---------------------------------------------------------------- S32
class TestS32LossReportRoundsOnce(_Base):
    def test_the_loss_report_adds_up_to_the_adjustment(self):
        it = self.item("Third")
        for i in range(3):
            _batch(self.db, self.ctx, it, 1, batch=f"X{i}", cost=str(Decimal(1) / 3), days=100 + i)
        v = adjust.adjust(self.db, self.ctx, it, qty_delta=-3, reason="damaged", note="كسر")["value"]
        rep = adjust.loss_report(self.db, self.ctx, local_today().isoformat(), local_today().isoformat())
        self.assertEqual(v, -100)
        self.assertEqual(rep["total"], v)
        self.assertEqual(rep["groups"][0]["value"], v)
        self.assertEqual(rep["total"], sum(g["value"] for g in rep["groups"] if g["reason"] != "supplier_return"))

    def test_separate_adjustments_keep_the_value_each_one_recorded(self):
        """Codex on #347: three one-unit losses at a third of a pound are 33 + 33 + 33 = 99, as their three
        audit records say; rounding the whole reason together turned them into 100."""
        it = self.item("Third")
        _batch(self.db, self.ctx, it, 3, batch="X0", cost=str(Decimal(1) / 3))
        day = local_today().isoformat()
        values = [adjust.adjust(self.db, self.ctx, it, qty_delta=-1, reason="damaged", note="كسر")["value"] for _ in range(3)]
        self.assertEqual(values, [-33, -33, -33])
        rep = adjust.loss_report(self.db, self.ctx, day, day)
        self.assertEqual((rep["total"], rep["groups"][0]["value"]), (-99, -99))

    def test_a_count_shortage_over_two_lots_is_one_loss(self):
        from app.inventory.counting import Counting
        it = self.item("Counted")
        for i in range(3):
            _batch(self.db, self.ctx, it, 1, batch=f"C{i}", cost=str(Decimal(1) / 3), days=100 + i)
        c = Counting(self.db)
        sid = c.create(self.ctx, title="جرد", scope="items", item_ids=[it])
        c.record(self.ctx, sid, item_id=it, qty=0)
        c.approve(self.ctx, sid)
        day = local_today().isoformat()
        self.assertEqual(adjust.loss_report(self.db, self.ctx, day, day)["total"], -100)

    def test_whole_piaster_costs_are_unchanged(self):
        it = self.item("Whole")
        _batch(self.db, self.ctx, it, 5, cost="12")
        adjust.adjust(self.db, self.ctx, it, qty_delta=-2, reason="lost")
        rep = adjust.loss_report(self.db, self.ctx, local_today().isoformat(), local_today().isoformat())
        self.assertEqual((rep["total"], rep["rows"][0]["value"]), (-2400, -2400))

    def test_a_movement_without_a_cost_is_counted_as_unknown_not_zero(self):
        it = self.item("No Cost")
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, movement_type="opening")
        adjust.adjust(self.db, self.ctx, it, qty_delta=-1, reason="lost")
        rep = adjust.loss_report(self.db, self.ctx, local_today().isoformat(), local_today().isoformat())
        self.assertEqual(rep["groups"][0]["unknown_cost"], 1)
        self.assertEqual(rep["total"], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
