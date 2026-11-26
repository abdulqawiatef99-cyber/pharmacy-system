"""Codex review #324 parts 2-4, group B (money/stock double posting, counting, purchase import): one test
per finding. Synthetic data only. Every test here failed on develop @ 1de18b8 (verified)."""
import io
import threading
import unittest
from datetime import date, timedelta
from decimal import Decimal

from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, ValidationFailed
from app.db import Database
from app.inventory import adjust, service as inv
from app.inventory.counting import Counting
from app.procurement import suppliers as sup
from app.purchasing import validation
from app.purchasing.importers.table import grid_to_invoice
from app.purchasing.model import ExtractedInvoice, ExtractedLine
from app.purchasing.service import Purchasing
from app.sales.cash import CashDrawer
from app.sales.service import Sales, local_today
from tests.helpers import DBTestCase
from tests.test_api import APICase, H
from tests.test_review_324_money import PW, _sell

T = date.today()


def _stock(db, ctx, item, qty, batch="B1", cost="15", days=400):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, batch, (T + timedelta(days=days)).isoformat())
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type="purchase",
                          unit_cost=Decimal(cost))
    return b


def _qty(db, ctx, item):
    return inv.stock_for_item(db, ctx, item)["total_qty"]


# ---------------------------------------------------------------- S6 / X6: supplier money posted twice
class TestS6X6SupplierRetry(APICase):
    def setUp(self):
        super().setUp()
        self.sid = cat.create_supplier(self.db, self.ctx, name="مورد المرتجع")
        self.lot = _stock(self.db, self.ctx, self.pan, 10)
        CashDrawer(self.db).open(self.ctx, 100000)

    def staff(self, deny):
        uid = people.add_user(self.db, self.ctx, username="mgr", full_name="mgr", password=PW, role_code="manager")
        people.set_exception(self.db, self.ctx, uid, deny, "deny")
        c = TestClient(self.app)
        self.http_login("mgr", PW, client=c)
        return c

    def test_payment_without_suppliers_read_posts_once_and_answers_success(self):
        c = self.staff("suppliers.read")
        hdr = {**H, "Idempotency-Key": "pay-1"}
        body = {"amount": 5000, "method": "drawer", "note": "دفعة مقدمة متفق عليها"}
        first = c.post(f"/api/suppliers/{self.sid}/pay", json=body, headers=hdr)
        self.assertEqual(first.status_code, 200, first.text)
        retry = c.post(f"/api/suppliers/{self.sid}/pay", json=body, headers=hdr)
        self.assertIn(retry.status_code, (200, 409), retry.text)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sid), -5000)
        self.assertEqual(self.db.one("SELECT count(*) n FROM cash_movements WHERE kind='payout'")["n"], 1)

    def test_return_without_purchases_read_posts_once_and_answers_success(self):
        c = self.staff("purchases.read")
        hdr = {**H, "Idempotency-Key": "ret-1"}
        body = {"supplier_id": self.sid, "lines": [{"item_id": self.pan, "batch_id": self.lot, "qty_base": 1,
                                                     "reason": "expired", "unit_value": 100}]}
        first = c.post("/api/supplier-returns", json=body, headers=hdr)
        self.assertEqual(first.status_code, 200, first.text)
        retry = c.post("/api/supplier-returns", json=body, headers=hdr)
        self.assertIn(retry.status_code, (200, 409), retry.text)
        self.assertEqual(_qty(self.db, self.ctx, self.pan), 9)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sid), -100)


    def test_a_rule_error_after_a_saved_write_keeps_the_key(self):
        """The wrapper itself, for any route: a handler that saved (with its audit row) and then raised a
        rule error must not free the key, or the retry runs the write again (#324 S6, same family as #331)."""
        from unittest import mock
        from app.core import audit
        from app.core.errors import Forbidden
        calls = []

        def saved_then_403(db, ctx, sid, amount, method, note=None, ref=None):
            with db.transaction():
                audit.record(db, ctx, "supplier.payment", "supplier", sid, after={"amount": amount})
            calls.append(1)
            raise Forbidden("بعد الحفظ", code="approval_required")
        self.http_login()
        hdr = {**H, "Idempotency-Key": "pay-wrapper-1"}
        with mock.patch("app.procurement.suppliers.pay", side_effect=saved_then_403):
            self.assertEqual(self.c.post(f"/api/suppliers/{self.sid}/pay", json={"amount": 1, "method": "cash"},
                                         headers=hdr).status_code, 403)
            retry = self.c.post(f"/api/suppliers/{self.sid}/pay", json={"amount": 1, "method": "cash"}, headers=hdr)
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertEqual(retry.json()["error"]["code"], "idempotency_unconfirmed")
        self.assertEqual(len(calls), 1)

    def test_a_rule_error_before_any_write_still_frees_the_key(self):
        self.http_login()
        hdr = {**H, "Idempotency-Key": "pay-refused-1"}
        r = self.c.post(f"/api/suppliers/{self.sid}/pay", json={"amount": 0, "method": "cash"}, headers=hdr)
        self.assertEqual(r.status_code, 422)
        r = self.c.post(f"/api/suppliers/{self.sid}/pay", json={"amount": 0, "method": "cash"}, headers=hdr)
        self.assertEqual(r.status_code, 422, r.text)              # ran again, not "unconfirmed"


# ---------------------------------------------------------------- S13-S16, X20: counting
class _Count(DBTestCase):
    def setUp(self):
        super().setUp()
        self.c = Counting(self.db)
        self.s = Sales(self.db)
        CashDrawer(self.db).open(self.ctx, 0)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Count Tab", "pack_size": 1, "public_price": 1000})
        self.batch = _stock(self.db, self.ctx, self.item, 10)


class TestS13CrossLocation(_Count):
    def test_a_sale_between_two_location_counts_is_not_deducted_twice(self):
        sid = self.c.create(self.ctx, title="جرد")
        self.c.record(self.ctx, sid, item_id=self.item, qty=5, location="A")
        _sell(self.s, self.ctx, self.item, 1)                    # sold from B, before B was counted
        self.c.record(self.ctx, sid, item_id=self.item, qty=4, location="B")
        try:
            rep = self.c.approve(self.ctx, sid)
        except Conflict as e:                                    # or: asked to recount, never a wrong figure
            self.assertEqual(e.code, "count_problems")
            return
        self.assertEqual(_qty(self.db, self.ctx, self.item), 9, rep)


class TestS14AddMode(_Count):
    def test_add_mode_never_puts_a_sold_unit_back(self):
        sid = self.c.create(self.ctx, title="جرد")
        self.c.record(self.ctx, sid, item_id=self.item, qty=9, mode="add")
        _sell(self.s, self.ctx, self.item, 1)                    # one of the nine already scanned
        try:
            self.c.record(self.ctx, sid, item_id=self.item, qty=1, mode="add")
            self.c.approve(self.ctx, sid)
        except Conflict:
            self.assertEqual(_qty(self.db, self.ctx, self.item), 9)
            return
        self.assertEqual(_qty(self.db, self.ctx, self.item), 9)


class TestS15ZeroOnlyWhatWasShown(_Count):
    def test_zeroing_uncounted_items_refuses_when_the_list_changed_after_the_preview(self):
        other = cat.create_item(self.db, self.ctx, {"name_en": "Other Tab", "pack_size": 1})
        late = cat.create_item(self.db, self.ctx, {"name_en": "Late Tab", "pack_size": 1})
        _stock(self.db, self.ctx, other, 3)
        sid = self.c.create(self.ctx, title="جرد")
        self.c.record(self.ctx, sid, item_id=self.item, qty=10)
        shown = [u["item_id"] for u in self.c.variances(self.ctx, sid)["uncounted_with_stock"]]
        self.assertEqual(shown, [other])
        _stock(self.db, self.ctx, late, 10)                     # a colleague receives a new item
        with self.assertRaises(Conflict) as e:
            self.c.approve(self.ctx, sid, zero_uncounted=True, reason="مش موجود على الرف",
                           uncounted_item_ids=shown)
        self.assertEqual(e.exception.code, "uncounted_changed")
        self.assertEqual(_qty(self.db, self.ctx, late), 10)

    def test_zeroing_needs_the_list_that_was_shown(self):
        sid = self.c.create(self.ctx, title="جرد")
        with self.assertRaises(ValidationFailed):
            self.c.approve(self.ctx, sid, zero_uncounted=True, reason="مش موجود على الرف")


class TestS16SetToRace(_Count):
    def test_two_set_to_requests_on_two_connections_end_at_the_requested_balance(self):
        """Both requests reach their write transaction together (a barrier at the first transaction() of each
        thread): the balance they act on must be read under the write lock, not before it."""
        from contextlib import contextmanager
        from unittest import mock
        db2 = Database(self.tmp / "test.db")
        ctx2 = self.login("owner", "Owner-Pass-2026")
        gate, seen = threading.Barrier(2), threading.local()
        real = Database.transaction

        @contextmanager
        def gated(db):
            if not getattr(seen, "done", False):
                seen.done = True
                gate.wait(timeout=5)
            with real(db) as d:
                yield d
        errs = []

        def run(db, ctx):
            try:
                adjust.adjust(db, ctx, self.item, set_to=12, reason="found")
            except Exception as e:      # noqa: BLE001 - the second one finds nothing left to change
                errs.append(e)
        with mock.patch.object(Database, "transaction", gated):
            ts = [threading.Thread(target=run, args=a) for a in ((self.db, self.ctx), (db2, ctx2))]
            [t.start() for t in ts]
            [t.join(15) for t in ts]
        db2.close()
        self.assertEqual(_qty(self.db, self.ctx, self.item), 12, errs)
        self.assertFalse(inv.verify_balances(self.db, self.ctx.org_id))


class TestX20ZeroUncountedString(APICase):
    def test_the_string_false_never_zeroes_anything(self):
        self.http_login()
        other = cat.create_item(self.db, self.ctx, {"name_en": "Other Tab", "pack_size": 1})
        _stock(self.db, self.ctx, other, 3)
        sid = Counting(self.db).create(self.ctx, title="جرد")
        r = self.c.post(f"/api/counts/{sid}/approve", json={"zero_uncounted": "false", "reason": "سبب مكتوب طويل"},
                        headers=H)
        self.assertIn(r.status_code, (200, 422), r.text)
        self.assertEqual(_qty(self.db, self.ctx, other), 3)


# ---------------------------------------------------------------- purchase import
def _lines(rows):
    return grid_to_invoice(rows).lines


class TestS20Bonus(unittest.TestCase):
    def test_qty_ten_plus_two_with_a_zero_bonus_column_keeps_the_two(self):
        l = _lines([["الصنف", "الكمية", "بونص", "سعر الجمهور"], ["Panadol", "10+2", 0, 40]])[0]
        self.assertEqual((l.qty, l.bonus_qty), (10, 2))


class TestS21PercentSign(unittest.TestCase):
    def test_half_a_percent_written_with_the_sign_stays_half_a_percent(self):
        l = _lines([["الصنف", "الكمية", "سعر الجمهور", "الخصم"], ["Panadol", 2, 100, "0.5%"]])[0]
        self.assertEqual(l.discount_pct, Decimal("0.5"))

    def test_a_fraction_column_is_still_converted(self):
        l = _lines([["الصنف", "الكمية", "سعر الجمهور", "الخصم"], ["Panadol", 2, 100, 0.12]])[0]
        self.assertEqual(l.discount_pct, Decimal("12.00"))


class TestS23LongPdf(unittest.TestCase):
    def test_a_pdf_longer_than_what_is_read_is_refused_not_cut(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab not installed (only needed to build the sample PDF)")
        from app.purchasing.importers import files
        buf = io.BytesIO()
        cv = canvas.Canvas(buf)
        for p in range(files.MAX_PDF_PAGES + 1):
            cv.drawString(72, 720, f"Panadol 500 mg     2     40.00     page {p + 1}")
            cv.showPage()
        cv.save()
        with self.assertRaises(ValidationFailed) as e:
            files.read_pdf(buf.getvalue())
        self.assertEqual(e.exception.code, "pdf_too_long")


class _Purchase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 1,
                                                        "public_price": 10000})
        self.sup = cat.create_supplier(self.db, self.ctx, name="ابن سينا")

    def manual(self, line, **header):
        iid = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, iid)["invoice"]["version"]
        v = self.p.add_line(self.ctx, iid, {"item_id": self.item, "batch_no": "X1", "expiry_date": "12/2029",
                                            **line}, v)["invoice"]["version"]
        d = self.p.update_header(self.ctx, iid, {"invoice_no": "77", "invoice_date": local_today().isoformat(),
                                                 **header}, v)
        return iid, d


class TestS24LowConfidenceSupplier(_Purchase):
    def test_a_supplier_read_with_low_confidence_is_not_linked(self):
        ex = ExtractedInvoice(method="ai_vision", supplier_name="ابن سينا", invoice_no="5",
                              invoice_date=local_today().isoformat(), declared_total=Decimal("100"),
                              header_confidence={"supplier_name": 0.1},
                              lines=[ExtractedLine(raw_name="Panadol 500mg 24 tab", qty=1,
                                                   unit_net_price=Decimal("100"), line_total=Decimal("100"))])
        iid = self.p._create_from_extraction(self.ctx, ex, "image", None, None)
        d = self.p.get(self.ctx, iid)
        self.assertIsNone(d["invoice"]["supplier_id"])
        self.assertFalse(d["can_approve"])


class TestS25AckFollowsTheDifference(_Purchase):
    def test_an_acknowledged_difference_does_not_cover_a_new_one(self):
        iid, d = self.manual({"qty": 1, "unit_net_price": 10000}, declared_total=10200)
        self.assertIn("total_mismatch", [i["code"] for i in d["issues"]])
        v = self.p.acknowledge(self.ctx, iid, "total_mismatch", "تقريب المورد", None,
                               d["invoice"]["version"])["invoice"]["version"]
        self.assertTrue(self.p.get(self.ctx, iid)["can_approve"])
        line = self.p.get(self.ctx, iid)["lines"][0]["id"]
        d = self.p.update_line(self.ctx, iid, line, {"qty": 10}, v)
        self.assertFalse(d["can_approve"])


class TestS26PreTaxTotal(unittest.TestCase):
    def test_a_printed_pre_tax_total_keeps_the_tax(self):
        line = {"unit_net_price": 10000, "qty": 1, "tax_amount": 50, "line_total": 10000}
        self.assertEqual(validation.effective_amount(line), 10050)


class TestS27InvoiceTaxInCost(_Purchase):
    def test_legacy_line_with_zero_price_and_additive_tax_still_can_post(self):
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Synthetic VAT-only unit", "public_price": 200})
        iid, draft = self.manual({"qty": 4}, declared_total=800)
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoice_lines SET unit_net_price=0,"
                            " tax_amount=800, tax_included=0, line_total=800 WHERE id=?",
                            (draft["lines"][0]["id"],))
        self.p.approve(self.ctx, iid, draft["invoice"]["version"])
        stock = inv.stock_for_item(self.db, self.ctx, self.item)
        self.assertEqual(Decimal(stock["avg_cost"]) * stock["total_qty"], Decimal("8.00"))

    def test_invoice_tax_on_zero_price_lines_reaches_the_unit_cost(self):
        iid = self.p.create_manual(self.ctx, supplier_id=self.sup)
        self.p.update_header(self.ctx, iid, {"invoice_no": "77", "extra_tax": 800, "declared_total": 800}, None)
        d = self.p.add_line(self.ctx, iid, {"item_id": self.item, "qty": 4, "public_price": 10000,
                                           "discount_bp": 10000, "expiry_date": "12/2029"}, None)
        self.p.approve(self.ctx, iid, d["invoice"]["version"])
        s = inv.stock_for_item(self.db, self.ctx, self.item)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), 800)
        self.assertEqual(Decimal(s["avg_cost"]) * s["total_qty"], Decimal("8.00"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- Codex on #336
class TestReview336AuditMarkPerPharmacy(APICase):
    """P1: the watermark that tells a saved write from a refused one reads this pharmacy's audit rows only
    (AGENTS.md: every query on pharmacy data filters by org_id), and by rowid, not over the whole log."""

    def setUp(self):
        super().setUp()
        self.sid = cat.create_supplier(self.db, self.ctx, name="مورد العلامة")

    def test_every_audit_query_of_an_idempotent_write_filters_by_pharmacy(self):
        seen, real = [], self.db.one

        def spy(sql, params=()):
            if "audit_log" in sql:
                seen.append(sql)
            return real(sql, params)
        self.http_login()
        self.db.one = spy
        try:
            r = self.c.post(f"/api/suppliers/{self.sid}/pay", json={"amount": 0, "method": "cash"},
                            headers={**H, "Idempotency-Key": "mark-1"})
        finally:
            del self.db.one
        self.assertEqual(r.status_code, 422)
        self.assertEqual(len(seen), 2, seen)                    # the mark and the "saved since" check
        for sql in seen:
            self.assertIn("org_id=?", sql)
            plan = " ".join(str(tuple(x)) for x in self.db.conn.execute("EXPLAIN QUERY PLAN " + sql,
                                                                         (1, 1, 1)[:sql.count("?")]))
            self.assertNotIn("ix_audit_org_entity", plan)       # never every audit row of the pharmacy


class TestReview336PriceBetweenLines(_Purchase):
    """P2: two lines of the same item on one invoice: each line's public price applies in order, and the
    price history records the price the line replaced, not the one read before the first line."""

    def test_the_last_line_price_wins_and_history_is_in_order(self):
        iid = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, iid)["invoice"]["version"]
        for batch, price in (("X1", 12000), ("X2", 10000)):
            v = self.p.add_line(self.ctx, iid, {"item_id": self.item, "qty": 1, "public_price": price,
                                                "unit_net_price": 9000, "batch_no": batch,
                                                "expiry_date": "12/2029"}, v)["invoice"]["version"]
        d = self.p.update_header(self.ctx, iid, {"invoice_no": "88", "invoice_date": local_today().isoformat(),
                                                 "declared_total": 18000}, v)
        self.p.approve(self.ctx, iid, d["invoice"]["version"])
        self.assertEqual(cat.get_item(self.db, self.ctx, self.item)["public_price"], 10000)
        hist = [(r["previous_price"], r["public_price"]) for r in self.db.all(
            "SELECT previous_price, public_price FROM item_price_history WHERE item_id=? AND source='purchase_invoice'"
            " ORDER BY rowid", (self.item,))]
        self.assertEqual(hist, [(10000, 12000), (12000, 10000)])


class TestReview336AckQueryPerPharmacy(_Purchase):
    """P1 (#336 review @ceaa15f): the lines read to find the difference being acknowledged are this
    pharmacy's only (AGENTS.md: every query on pharmacy data filters by org_id)."""

    def test_the_acknowledgement_reads_lines_by_pharmacy(self):
        iid, d = self.manual({"qty": 1, "unit_net_price": 10000}, declared_total=10200)
        seen, real = [], self.db.all

        def spy(sql, params=()):
            if "purchase_invoice_lines" in sql:
                seen.append(sql)
            return real(sql, params)
        self.db.all = spy
        try:
            self.p.acknowledge(self.ctx, iid, "total_mismatch", "تقريب المورد", None, d["invoice"]["version"])
        finally:
            del self.db.all
        self.assertTrue(seen)
        for sql in seen:
            self.assertIn("org_id=?", sql, sql)
