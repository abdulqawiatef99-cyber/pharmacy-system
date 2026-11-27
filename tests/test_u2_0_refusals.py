"""U2-0 «امنع الغلط، متنبهش عليه» (OWNER_RULES §13): an impossible or illogical money / stock entry is REFUSED
by the server, not warned about. One test per entry point; each must refuse."""
from dataclasses import replace
from datetime import timedelta

from app.catalog import service as cat
from app.core.errors import DomainError as AppError
from app.sales.cash import CashDrawer
from tests.test_sales import TODAY, SalesCase


class Refused(SalesCase):
    def refund_approval(self):
        return replace(self.ctx, approver_id="manager-fixture",
                       approver_permissions=frozenset({"sales.refund_other_method"}))

    def refuses(self, fn, *a, **kw):
        with self.assertRaises(AppError):
            fn(*a, **kw)

    # ------------------------------------------------------------------ sale
    def test_sale_beyond_stock(self):
        sid = self.s.create(self.ctx)
        self.refuses(self.s.add_line, self.ctx, sid, self.pan, units=14)          # 13 in stock
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.refuses(self.s.update_line, self.ctx, sid, d["lines"][0]["id"], qty_base=14)

    def test_zero_or_negative_quantity(self):
        sid = self.s.create(self.ctx)
        self.refuses(self.s.add_line, self.ctx, sid, self.pan, units=0)
        self.refuses(self.s.add_line, self.ctx, sid, self.pan, units=-1)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        self.refuses(self.s.update_line, self.ctx, sid, d["lines"][0]["id"], qty_base=0)
        self.refuses(self.s.update_line, self.ctx, sid, d["lines"][0]["id"], qty_base=-3)

    def test_item_price_zero_or_negative(self):
        self.refuses(cat.create_item, self.db, self.ctx, {"name_en": "Neg Price", "public_price": -100})

    def test_line_discount_above_line_value(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)             # 19.00
        self.refuses(self.s.update_line, self.ctx, sid, d["lines"][0]["id"], discount_amount=d["total"] + 1)

    def test_invoice_discount_above_invoice_value(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        self.refuses(self.s.set_invoice_discount, self.ctx, sid, amount=d["total"] + 1)

    def test_credit_sale_without_customer(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        self.refuses(self.s.complete, self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))

    def _sold(self, **paid):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        pay = {k: (d["total"] if v == "all" else v) for k, v in paid.items()}
        self.s.complete(self.ctx, sid, safety_decisions=self.ack_all(sid), **pay)
        return sid, d

    def test_return_above_sold_minus_returned(self):
        sid, d = self._sold(paid_cash="all")
        lid = d["lines"][0]["id"]
        self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="cash", reason="test")
        self.refuses(self.s.return_items, self.ctx, sid, {lid: 2}, refund_method="cash", reason="test")

    def test_card_refund_of_a_cash_sale(self):
        sid, d = self._sold(paid_cash="all")
        self.refuses(self.s.return_items, self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="card", reason="test")

    def test_split_invoice_partial_returns_by_method(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=4)                 # 4 strips
        cash_part = d["total"] // 2
        self.s.complete(self.ctx, sid, paid_cash=cash_part, paid_card=d["total"] - cash_part,
                        safety_decisions=self.ack_all(sid))
        lid = d["lines"][0]["id"]
        self.s.return_items(self.ctx, sid, {lid: 1}, refund_method="card", reason="test")    # one strip to the card
        with self.assertRaises(AppError):                                                   # two more: above the card
            self.s.return_items(self.ctx, sid, {lid: 2}, refund_method="card", reason="test")
        self.s.return_items(self.ctx, sid, {lid: 2}, refund_method="cash", reason="test")    # the cash part

    def test_exchange_of_a_card_invoice_through_cash_needs_approval(self):
        """Codex P1 on #403: an exchange records the returned money as a cash payment of the replacement, whose own
        return could then pay it out in cash. Card money turns into cash only with an approval, in the preview too."""
        from dataclasses import replace
        sid, d = self._sold(paid_card="all")
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"sales.refund_other_method"})
        new = self.s.create(self.ctx)
        self.s.add_line(self.ctx, new, self.pan, units=2)
        for call in (lambda c: self.s.return_value(c, sid, {d["lines"][0]["id"]: 1}, refund_method="cash"),
                     lambda c: self.s.exchange(c, sid, {d["lines"][0]["id"]: 1}, new, refund_method="cash",
                                               pay_method="card", reason="تغيير", safety_decisions=self.ack_all(new))):
            with self.assertRaises(AppError) as e:
                call(staff)
            self.assertEqual(e.exception.details["permission"], "sales.refund_other_method")
        out = self.s.exchange(self.refund_approval(), sid, {d["lines"][0]["id"]: 1}, new,
                              refund_method="cash", pay_method="card",
                              reason="تغيير", safety_decisions=self.ack_all(new))
        self.assertEqual(out["settled"]["refund"], 0)

    def test_invoice_discount_left_bigger_after_line_removed(self):
        sid = self.s.create(self.ctx)
        with self.db.transaction():   # U2-33: nobody sells under cost; this test is about the invoice discount, so the cost is unknown
            self.db.execute("DELETE FROM item_costs WHERE item_id=?", (self.pan,))
        d = self.s.add_line(self.ctx, sid, self.pan, units=4)
        d = self.s.set_invoice_discount(self.ctx, sid, amount=d["total"] - 100)
        d = self.s.update_line(self.ctx, sid, d["lines"][0]["id"], qty_base=4)      # unchanged: fine
        with self.db.transaction():                                              # lines changed behind the discount
            self.db.execute("UPDATE sale_lines SET qty_base=1 WHERE sale_id=?", (sid,))
        d = self.s.get(self.ctx, sid)
        with self.assertRaises(AppError) as e:
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual(e.exception.code, "discount_exceeds_value")

    # ------------------------------------------------------------------ money in / out
    def test_customer_payment_above_due(self):
        c = self.s.create_customer(self.ctx, name="عميل اختبار")
        self.refuses(self.s.customer_payment, self.ctx, c, 100)

    def test_negative_drawer_count(self):
        self.refuses(CashDrawer(self.db).close, self.ctx, denominations={"100": -1, "50": 2})

    def test_supplier_payment_above_due(self):
        from app.procurement import suppliers
        sup = cat.create_supplier(self.db, self.ctx, name="مورد اختبار")
        from dataclasses import replace
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"suppliers.pay_advance"})
        with self.assertRaises(AppError) as e:
            suppliers.pay(self.db, staff, sup, 100000, "cash", note="دفعة مقدمة")
        self.assertEqual(e.exception.details["permission"], "suppliers.pay_advance")    # a manager can approve it
        with self.assertRaises(AppError) as e:
            suppliers.pay(self.db, self.ctx, sup, 100000, "cash")                     # owner: reason needed
        self.assertEqual(e.exception.code, "reason_required")
        suppliers.pay(self.db, self.ctx, sup, 100000, "cash", note="دفعة مقدمة متفق عليها")   # the exception
        self.assertEqual(suppliers.balance(self.db, self.ctx.org_id, sup), -100000)

    def test_cash_refund_of_a_card_sale_needs_permission(self):
        sid, d = self._sold(paid_card="all")
        from dataclasses import replace
        staff = replace(self.ctx, permissions=self.ctx.permissions - {"sales.refund_other_method"})
        with self.assertRaises(AppError) as e:
            self.s.return_items(staff, sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        self.assertEqual(e.exception.details["permission"], "sales.refund_other_method")
        r = self.s.return_items(self.refund_approval(), sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="test")
        self.assertGreater(r["refund"], 0)


class PurchaseRefused(SalesCase):
    def setUp(self):
        super().setUp()
        from dataclasses import replace
        from app import config
        from app.purchasing.service import Purchasing
        self.pur = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد فاتورة")

    def draft(self, **hdr):
        inv_id = self.pur.create_manual(self.ctx)
        self.pur.update_header(self.ctx, inv_id, {"supplier_id": self.sup, "invoice_no": hdr.get("no", "A1"),
                                                  "invoice_date": hdr.get("date", TODAY.isoformat())}, None)
        return inv_id

    def test_supplier_invoice_date_in_future(self):
        inv_id = self.pur.create_manual(self.ctx)
        self.refuses(self.pur.update_header, self.ctx, inv_id, {"invoice_date": (TODAY + timedelta(days=2)).isoformat()}, None)

    def refuses(self, fn, *a, **kw):
        with self.assertRaises(AppError):
            fn(*a, **kw)

    def _line(self, inv_id, expiry):
        return self.pur.add_line(self.ctx, inv_id, {"item_id": self.pan, "qty": 1, "public_price": "40",
                                                    "discount_pct": "20", "batch_no": "B1", "expiry_date": expiry}, None)

    def test_expired_purchase_line_cannot_be_approved(self):
        inv_id = self.draft(no="EXP1")
        expired = (TODAY - timedelta(days=3)).isoformat()
        self.refuses(self._line, inv_id, expired)
        line = self._line(inv_id, (TODAY + timedelta(days=400)).isoformat())["lines"][0]
        with self.db.transaction():
            self.db.execute("UPDATE purchase_invoice_lines SET expiry_date=? WHERE id=?", (expired, line["id"]))
        self.refuses(self.pur.approve, self.ctx, inv_id, None)

    def test_duplicate_supplier_invoice_number(self):
        self.draft(no="DUP")
        self.refuses(self.draft, no="DUP")

    def test_distributor_margin_negative_or_above_half_the_public_price(self):
        """B3: a negative margin or a margin above half the public price is refused at entry."""
        inv_id = self.draft(no="MRG")
        line = self._line(inv_id, (TODAY + timedelta(days=400)).isoformat())["lines"][0]
        self.refuses(self.pur.update_line, self.ctx, inv_id, line["id"], {"distributor_margin": -100}, None)
        self.refuses(self.pur.update_line, self.ctx, inv_id, line["id"], {"distributor_margin": 2100}, None)



class TestKeepChangeIsOnlyChange(__import__("tests.test_sales", fromlist=["SalesCase"]).SalesCase):
    """«خليها رصيد للعميل»: more than real change (50 EGP, below the cash paid) is an advance, refused here."""
    def test_refused(self):
        from app.core.errors import ValidationFailed
        from app.db import new_id
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit) VALUES (?,?,?,?)",
                            (cid, self.ctx.org_id, "عميل", 0))
        sid = self.s.create(self.ctx, cid)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1)
        with self.assertRaises(ValidationFailed) as e:
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid), change_to_credit=100_000)
        self.assertEqual(e.exception.code, "keep_change_too_big")
