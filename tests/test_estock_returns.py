"""Return against an old E-Stock invoice (ج1, docs/specs/09-estock-old-invoice-returns.md). Synthetic data only."""
from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.sales import estock_returns as er
from app.sales.cash import CashDrawer
from tests.test_estock_db_import import Base, base_tables

PW = "Staff-Pass-2026"


def tables(**over):
    t = base_tables()
    h = t["Sales_header"][0]
    h.update({"total_bill_net": "65.0000", "bill_cash": "20.0000", "network_money": "15.0000", "money_change": "0",
              "insert_date": "2026-10-01T10:00:00.000"})
    d = t["Branches_sales_details"]
    d[0].update({"counter_id": "11", "total_sell": "30.0000", "buy_price": "24.0000", "exp_date": "2027-05-01T00:00:00"})
    d[1].update({"counter_id": "12", "total_sell": "35.0000", "buy_price": "140.0000", "exp_date": "2027-01-01T00:00:00",
                 "sale_unit_change": "14"})
    t["Back_sales_header"], t["Back_Sales_details"] = [], []
    t.update(over)
    return t


class _Base(Base):
    TABLES = None

    def setUp(self):
        super().setUp()
        self.assertTrue(self.run_import(tables=self.TABLES() if self.TABLES else tables())["ok"])
        CashDrawer(self.db).open(self.ctx, 0)

    def ret(self, lines, key="k1", ctx=None, reason="مرتجع عميل"):
        return er.return_items(self.db, ctx or self.ctx, "1", lines, reason=reason, key=key)

    def ledger(self):
        cid = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        return [r["amount"] for r in self.db.all("SELECT amount FROM customer_ledger WHERE customer_id=? AND kind='return'", (cid,))]

    def cash(self):
        return [r["amount"] for r in self.db.all("SELECT amount FROM cash_movements WHERE kind='refund'")]


class TestReturn(_Base):
    def test_lines_listed_with_what_is_left(self):
        lines = {l["line_key"]: l for l in er.invoice_lines(self.db, self.ctx, "1")}
        self.assertEqual((lines["2"]["sold_units"], lines["2"]["returned_units"], lines["2"]["net"]), (7, 0, 3500))

    def test_full_line_back_the_way_it_was_paid_account_first(self):
        r = self.ret({"2": 7})
        self.assertEqual((r["refund"], r["parts"]), (3500, {"account": 3000, "cash": 500, "card": 0}))
        self.assertEqual(self.ledger(), [-3000])
        self.assertEqual(self.cash(), [-500])
        m = self.db.one("SELECT m.qty_delta, m.unit_cost, m.movement_type, b.batch_no, b.expiry_date FROM stock_movements m"
                        " JOIN batches b ON b.id=m.batch_id WHERE m.ref_type='estock_sale_return'")
        self.assertEqual((m["qty_delta"], m["unit_cost"], m["movement_type"], m["batch_no"], m["expiry_date"]),
                         (7, "10.000000", "sale_return", "1-12", "2027-01-01"))
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action='estock.sale_return'")["n"], 1)

    def test_same_key_writes_nothing_twice(self):
        first = self.ret({"2": 7})
        again = self.ret({"2": 7})
        self.assertEqual(first, again)
        self.assertEqual((self.ledger(), self.cash()), ([-3000], [-500]))
        self.assertEqual(self.db.one("SELECT count(*) n FROM stock_movements WHERE ref_type='estock_sale_return'")["n"], 1)

    def test_partial_returns_add_up_to_the_line_exactly(self):
        a = self.ret({"1": 1}, key="a")                       # 1 of 3 units of a 30.00 line
        b = self.ret({"1": 2}, key="b")                       # the rest: exactly what is left
        self.assertEqual((a["refund"], b["refund"]), (1000, 2000))
        with self.assertRaises(Conflict):
            self.ret({"1": 1}, key="c")                       # nothing left

    def test_more_than_was_sold_is_refused(self):
        with self.assertRaises(Conflict):
            self.ret({"2": 8})
        for bad in ({"2": 0}, {"2": 1.5}, {"2": True}, {}, {"99": 1}):
            with self.assertRaises((ValidationFailed, NotFound)):
                self.ret(bad, key=str(bad))
        with self.assertRaises(ValidationFailed):
            self.ret({"2": 1}, reason="")
        with self.assertRaises(ValidationFailed):
            er.return_items(self.db, self.ctx, "1", {"2": 1}, reason="مرتجع", key="")

    def test_permissions_and_other_pharmacy(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        with self.assertRaises(Forbidden):
            self.ret({"2": 1}, ctx=self.login("asst", PW))
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            self.ret({"2": 1}, ctx=other)

    def test_a_cash_part_needs_an_open_shift(self):
        CashDrawer(self.db).close(self.ctx, 0, note="آخر اليوم")
        with self.assertRaises(Conflict):
            self.ret({"2": 7})                                # 5.00 of it would come from the drawer
        self.assertEqual(self.ledger(), [])                   # nothing written


class TestWhatEStockAlreadyReturned(_Base):
    TABLES = staticmethod(lambda: tables(
        Back_sales_header=[{"back_sales_id": "1", "sales_id": "1", "customer_id": "1", "total_bill_net": "30.0000"}],
        Back_Sales_details=[{"back_sales_id": "1", "sales_id": "1", "product_id": "1", "back_amount": "1"}]))

    def test_estock_returns_count_for_quantity_and_money(self):
        with self.assertRaises(Conflict):
            self.ret({"1": 1})                                # E-Stock already took the 3 units back
        r = self.ret({"2": 7})                 # 35.00; E-Stock already refunded 30.00, from the account part first
        self.assertEqual(r["parts"], {"account": 0, "cash": 2000, "card": 1500})


class TestNotMoreThanReceived(_Base):
    TABLES = staticmethod(lambda: tables(Sales_header=[{"sales_id": "1", "customer_id": "0", "total_bill_net": "65.0000",
                                                         "bill_cash": "10.0000", "network_money": "0",
                                                         "insert_date": "2026-10-01T10:00:00.000"}]))

    def test_no_customer_and_only_ten_paid(self):
        with self.assertRaises(Conflict) as e:
            self.ret({"2": 7})                                # 35.00 back, 10.00 received, no account to credit
        self.assertEqual(e.exception.code, "refund_exceeds_received")
        self.assertEqual(self.cash(), [])
        r = self.ret({"1": 1}, key="small")                   # 10.00: the cash that was paid
        self.assertEqual(r["parts"], {"account": 0, "cash": 1000, "card": 0})


class TestCodexReview346(_Base):
    """Codex on #346 @d884121 (each failed before the fix)."""

    def test_p1_a_no_return_category_needs_the_restricted_permission(self):
        from app.catalog import card
        card.ensure_default_categories(self.db)
        cat = {c["name"]: c for c in card.list_categories(self.db, self.ctx)}["ثلاجة"]
        self.assertTrue(cat["no_return"])
        with self.db.transaction():
            self.db.execute("UPDATE items SET category_id=? WHERE code='102'", (cat["id"],))
        import dataclasses
        people.add_user(self.db, self.ctx, username="pharm1", full_name="P", password=PW, role_code="pharmacist")
        ph = self.login("pharm1", PW)                  # every return permission but the restricted one
        ph = dataclasses.replace(ph, permissions=ph.permissions | {"sales.return_others", "sales.return_late"})
        self.assertFalse(ph.allowed("sales.return_restricted"))
        with self.assertRaises(Forbidden) as e:
            self.ret({"2": 7}, ctx=ph)
        self.assertEqual(e.exception.details["permission"], "sales.return_restricted")
        self.assertEqual(self.ledger(), [])
        self.assertTrue(self.ret({"2": 7})["refund"])                    # the owner may

    def test_p2_a_service_line_returned_in_full_cannot_be_returned_again(self):
        with self.db.transaction():
            self.db.execute("UPDATE items SET is_service=1 WHERE code='102'")
        self.ret({"2": 7}, key="a")
        lines = {l["line_key"]: l for l in er.invoice_lines(self.db, self.ctx, "1")}
        self.assertEqual(lines["2"]["returned_units"], 7)
        with self.assertRaises(Conflict):
            self.ret({"2": 1}, key="b")


class TestCodexReview346Repeated(_Base):
    """One E-Stock return of a product sold on two lines counts once, not on each line."""
    TABLES = staticmethod(lambda: _repeated())

    def test_p2_b_historical_return_allocated_once(self):
        lines = {l["line_key"]: l for l in er.invoice_lines(self.db, self.ctx, "1")}
        self.assertEqual(lines["1"]["returned_units"] + lines["3"]["returned_units"], 3)
        r = self.ret({"3": 3} if lines["3"]["returned_units"] == 0 else {"1": 3})
        self.assertEqual(r["refund"], 3000)


def _repeated():
    t = tables()
    t["Sales_header"][0].update({"total_bill_net": "95.0000", "bill_cash": "50.0000"})
    t["Branches_sales_details"].append(dict(t["Branches_sales_details"][0], details_id="3", counter_id="13"))
    t["Back_sales_header"] = [{"back_sales_id": "1", "sales_id": "1", "customer_id": "1", "total_bill_net": "30.0000"}]
    t["Back_Sales_details"] = [{"back_sales_id": "1", "sales_id": "1", "product_id": "1", "back_amount": "1"}]
    return t


class TestCodexReview346b(_Base):
    """Codex on #346 @7ac34a1 (failed before the fix)."""

    def test_p2_one_line_under_two_key_forms_is_refused(self):
        with self.assertRaises(ValidationFailed):
            self.ret({"1": 3, 1: 3})
        self.assertEqual((self.ledger(), self.cash()), ([], []))
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements WHERE ref_type='estock_sale_return'"))
        r = self.ret({1: 3}, key="int")                       # an int key alone is the same line
        self.assertEqual(r["lines"], {"1": 3})
