"""Codex review #324 parts 2-4, group D (after launch, owner decides): reproductions of the findings that
are verified but not fixed before launch. Each test states the correct behaviour and is marked
expectedFailure: it fails today, and the suite turns red ("unexpected success") the day the defect is
fixed, so the mark is removed with the fix. Synthetic data only."""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import alternatives, gs1, service as cat
from app.inventory import adjust, service as inv
from app.procurement import returns, suppliers as sup
from app.procurement.service import Procurement
from app.purchasing import validation
from app.purchasing.service import Purchasing
from app.sales.service import local_today
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


class TestS5X11ForeignSupplier(_Base):
    @known
    def test_a_manual_invoice_cannot_use_another_pharmacys_supplier(self):
        _, other = self.second_tenant()
        foreign = cat.create_supplier(self.db, other, name="مورد صيدلية تانية")
        with self.assertRaises(Exception):
            Purchasing(self.db).create_manual(self.ctx, supplier_id=foreign)


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


class TestS19SupplierChangeKeepsMapping(_Base):
    @known
    def test_changing_the_supplier_rematches_the_lines(self):
        from app.purchasing.model import ExtractedInvoice, ExtractedLine
        p = Purchasing(self.db)
        i100 = self.item("Zeta 100mg")
        i200 = self.item("Omega 200mg")
        a = cat.create_supplier(self.db, self.ctx, name="مورد أ")
        b = cat.create_supplier(self.db, self.ctx, name="مورد ب")
        with self.db.transaction():
            for s_, i_ in ((a, i100), (b, i200)):
                self.db.execute("INSERT INTO supplier_item_codes (org_id, supplier_id, code, item_id) VALUES (?,?,?,?)",
                                (self.ctx.org_id, s_, "SKU9", i_))
        ex = ExtractedInvoice(method="structured", lines=[ExtractedLine(raw_name="unknown name", raw_code="SKU9", qty=1)])
        iid = p._create_from_extraction(self.ctx, ex, "excel", None, a)
        self.assertEqual(p.get(self.ctx, iid)["lines"][0]["item_id"], i100)
        d = p.update_header(self.ctx, iid, {"supplier_id": b}, p.get(self.ctx, iid)["invoice"]["version"])
        self.assertEqual(d["lines"][0]["item_id"], i200)


class TestS22InferredNetRounding(unittest.TestCase):
    @known
    def test_inferred_net_uses_the_discount_of_rule(self):
        from app.sales.service import discount_of
        line = validation.derive_line({"public_price": 101, "discount_bp": 5000, "qty": 1,
                                 "field_sources": "{}", "field_confidence": "{}"})
        self.assertEqual(line["unit_net_price"], 101 - discount_of(101, 5000))


class TestS28CancelAfterReturn(_Base):
    @known
    def test_cancelling_a_purchase_leaves_a_later_receipt_and_an_earlier_credit_alone(self):
        p = Purchasing(self.db)
        it = self.item("Cancel Tab")
        a = cat.create_supplier(self.db, self.ctx, name="أ")
        iid = p.create_manual(self.ctx, supplier_id=a)
        v = p.get(self.ctx, iid)["invoice"]["version"]
        v = p.add_line(self.ctx, iid, {"item_id": it, "qty": 10, "unit_net_price": 100, "batch_no": "L1",
                                       "expiry_date": "12/2029"}, v)["invoice"]["version"]
        v = p.update_header(self.ctx, iid, {"invoice_no": "1", "invoice_date": local_today().isoformat(),
                                            "declared_total": 1000}, v)["invoice"]["version"]
        v = p.approve(self.ctx, iid, v)["invoice"]["version"]
        batch = self.db.one("SELECT id FROM batches WHERE item_id=?", (it,))["id"]
        returns.create(self.db, self.ctx, a, [{"item_id": it, "batch_id": batch, "qty_base": 4, "reason": "expired"}])
        # supplier B then delivers four units of the same lot
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=4, batch_id=batch, movement_type="purchase",
                              unit_cost=Decimal("1"))
        p.cancel(self.ctx, iid, "إلغاء تجربة", v)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, it)["total_qty"], 4)     # B's four stay
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, a), 0)


class TestS29UntrackedReturn(_Base):
    @known
    def test_returning_untracked_units_takes_untracked_stock(self):
        it = self.item("Mixed Tab")
        a = cat.create_supplier(self.db, self.ctx, name="أ")
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=None, movement_type="purchase",
                              unit_cost=Decimal("1"))
        tracked = _batch(self.db, self.ctx, it, 3, batch="T1", days=100)
        returns.create(self.db, self.ctx, a, [{"item_id": it, "qty_base": 2, "reason": "expired", "unit_value": 100}])
        self.assertEqual(inv.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, it, tracked), 3)


class TestS30ReturnPriceOfAnotherSupplier(_Base):
    @known
    def test_the_suggested_value_is_what_this_supplier_was_paid(self):
        it = self.item("Lot Tab")
        b = _batch(self.db, self.ctx, it, 5, batch="L1", cost="12")
        __import__("time").sleep(0.01)                                    # a later receipt, by its clock
        _batch(self.db, self.ctx, it, 5, batch="L1", cost="18")          # same lot, later, other supplier
        self.assertEqual(returns.suggest_value(self.db, self.ctx, it, b), 1200)


class TestS31WholePackReturn(_Base):
    @known
    def test_a_whole_pack_is_credited_what_it_cost(self):
        it = self.item("Pack3", pack=3, price=300)
        a = cat.create_supplier(self.db, self.ctx, name="أ")
        b = _batch(self.db, self.ctx, it, 3, cost=str(Decimal(1) / 3))   # 100 piasters for the pack
        r = returns.create(self.db, self.ctx, a, [{"item_id": it, "batch_id": b, "qty_base": 3, "reason": "expired"}])
        self.assertEqual(r["total"], 100)


class TestS32LossValue(_Base):
    @known
    def test_the_loss_report_adds_up_to_the_adjustment(self):
        it = self.item("Third")
        for i in range(3):
            _batch(self.db, self.ctx, it, 1, batch=f"X{i}", cost=str(Decimal(1) / 3), days=100 + i)
        v = adjust.adjust(self.db, self.ctx, it, qty_delta=-3, reason="damaged", note="كسر")["value"]
        rep = adjust.loss_report(self.db, self.ctx, local_today().isoformat(), local_today().isoformat())
        self.assertEqual(rep["total"], v)


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
