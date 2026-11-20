"""Manager 20/11 15:15, from the cashier-PC rehearsal with E-Stock closed: the two "duplicates" that stopped
the move were not a read taken while E-Stock sold. Sales_details numbers its lines per invoice, and one stock
row is really twice inside E-Stock. And the owner settles old accounts from the dates of their last invoice
and payment. Synthetic data only; each test failed before the change."""
import json

from tests.test_estock_db_import import SOLD, Base, base_tables, stock_row
from tests.test_estock_owner_report import _R


def sales_details(per_invoice: int = 3, invoices: int = 2) -> list[dict]:
    """E-Stock's Sales_details: details_id counts 1, 2, 3 inside each invoice (per-invoice line numbers)."""
    return [{"sales_id": str(s), "details_id": str(n), "product_id": "1", "amount": "1", "sell_price": "30.0000",
             "total_sell": "30.0000", "insert_date": SOLD}
            for s in range(1, invoices + 1) for n in range(1, per_invoice + 1)]


class TestPerInvoiceLineNumbers(Base):
    def test_line_numbers_repeating_across_invoices_are_not_duplicates(self):
        t = base_tables()
        t["Sales_details"] = sales_details()
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["plan"].blocking)
        self.assertFalse([d for d in res["plan"].dq if d["table"] == "Sales_details" and "مكرر" in d["action"]])

    def test_the_same_invoice_line_twice_is_still_found(self):
        t = base_tables()
        t["Sales_details"] = sales_details() + [dict(sales_details()[0], total_sell="31.0000")]
        res = self.run_import(tables=t)
        # both rows of the clashing line are set apart (Codex on #385) and listed for review
        line = [d for d in res["plan"].dq if d["table"] == "Sales_details" and d["field"] == "same id, other cells"]
        self.assertTrue(line, res["plan"].dq)
        self.assertEqual(line[0]["estock_value"], "2 سطر")


class TestDuplicateStockRow(_R):
    def test_two_identical_rows_count_once_and_do_not_stop_the_move(self):
        t = base_tables()
        t["Product_Amount"].append(dict(t["Product_Amount"][0]))            # pa_id 1 twice, the same cells
        txt = self.report(tables=t)
        self.assertTrue(self.res["ok"], self.res["plan"].blocking)
        units = self.db.one("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements m JOIN items i ON i.id=m.item_id"
                            " WHERE i.code='100'")["q"]
        self.assertEqual(units, self._units_without_copy())                    # counted once
        sec3 = self.section(txt, "٣. الباقي", None)
        self.assertIn("مكرر", sec3)
        self.assertNotIn("مكرر", self.section(txt, "١. محتاج قرارك", "٢. المطابقة"))

    def _units_without_copy(self):
        from tests.helpers import DBTestCase
        self.db.close()
        DBTestCase.setUp(self)
        self.run_import(tables=base_tables())
        return self.db.one("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements m JOIN items i ON i.id=m.item_id"
                           " WHERE i.code='100'")["q"]

    def test_two_different_rows_stop_and_show_both_for_a_one_line_decision(self):
        t = base_tables()
        t["Product_Amount"].append(stock_row(1, 1, 1, "4", "22.0000", "2027-09-01T00:00:00"))   # pa_id 1, other cells
        txt = self.report(tables=t)
        self.assertFalse(self.res["ok"])
        e = [x for x in self.entries(self.section(txt, "١. محتاج قرارك", "٢. المطابقة")) if "مكرر" in x][0]
        for want in ("«Testamol 500 Tab»", "كود 100", "2027-05-01", "2027-09-01", "24", "22", '"duplicates"'):
            self.assertIn(want, e)
        self.assertNotIn("شغال", e)                                              # E-Stock was closed

    def test_the_owners_choice_in_packs_json_settles_it(self):
        t = base_tables()
        t["Product_Amount"].append(stock_row(1, 1, 1, "4", "22.0000", "2027-09-01T00:00:00"))
        dec = self.tmp / "packs.json"
        dec.write_text(json.dumps({"duplicates": {"1": 2}}), encoding="utf-8")
        self.report(tables=t, packs_file=dec)
        self.assertTrue(self.res["ok"], self.res["plan"].blocking)
        # pa_id 1 posts once, from row 2 (the owner's choice): its expiry and quantity, not row 1's
        pa1 = [r for r in self.res["plan"].stock if r["eid"] == "1"]
        self.assertEqual(len(pa1), 1)
        self.assertEqual((pa1[0]["expiry"] or "")[:7], "2027-09")


class TestNoBlameOnARunningEStock(Base):
    def test_no_line_says_the_read_was_taken_while_estock_ran(self):
        t = base_tables()
        t["Product_Amount"].append(stock_row(1, 1, 1, "4", "22.0000", "2027-09-01T00:00:00"))
        t["Gedo_Financial"] = [{"gf_id": "1", "gf_for_me": "5", "insert_date": "2026-11-01T10:00:00"}]
        res = self.run_import(tables=t)
        said = " ".join(d["action"] for d in res["plan"].dq)
        self.assertNotIn("وإي ستوك شغال", said)
        ends = [d for d in res["plan"].dq if d["field"] == "insert_date" and d["table"] == "export"]
        if ends:
            self.assertIn("آخر حركة", ends[0]["action"])


class TestLastActivityForSettlement(_R):
    def test_the_sheet_and_the_report_show_last_invoice_and_last_payment(self):
        from openpyxl import load_workbook
        t = base_tables()
        t["Purchase_header"] += [{"purchase_id": "2", "vendor_id": "1", "total_bill": "50.0000",
                                  "bill_date": "2025-03-10T00:00:00"}]
        t["Gedo_Vendors"][0]["insert_date"] = "2025-04-01T09:00:00"            # the payment row (-100)
        t["Gedo_customers"][0]["insert_date"] = "2026-10-05T09:00:00"
        txt = self.report(tables=t)
        ws = load_workbook(self.res["reports"]["openings"]).active
        head = [c.value for c in ws[1]]
        self.assertIn("آخر فاتورة", head)
        self.assertIn("آخر دفعة", head)
        rows = {(r[0], r[1]): r for r in ws.iter_rows(min_row=2, values_only=True)}
        sup = rows[("مورد", "1")]
        self.assertEqual(sup[head.index("آخر دفعة")], "2025-04-01")
        self.assertEqual(sup[head.index("آخر فاتورة")], max("2025-03-10", Base_OLD()[:10]))
        cus = rows[("عميل", "1")]
        self.assertEqual(cus[head.index("آخر فاتورة")], SOLD[:10])
        self.assertEqual(cus[head.index("آخر دفعة")], "2026-10-05")
        bal = [r[head.index("رصيد إي ستوك")] for r in ws.iter_rows(min_row=2, values_only=True)]
        self.assertEqual(bal, sorted(bal, reverse=True))                       # largest balance first
        self.assertIn("آخر دفعة 2025-04-01", self.section(txt, "٣. الباقي", None))


def Base_OLD() -> str:
    from tests.test_estock_db_import import OLD
    return OLD


if __name__ == "__main__":       # pragma: no cover
    import unittest
    unittest.main()


class TestMasterDuplicates(Base):
    def test_an_item_twice_stops_even_when_identical(self):
        t = base_tables()
        t["Products"].append(dict(t["Products"][1]))                       # product 2 twice, the same cells
        res = self.run_import(tables=t)
        self.assertIn(("Products", "product_id"), {(b["table"], b["field"]) for b in res["plan"].blocking})
        self.assertFalse(self.db.one("SELECT 1 FROM items"))


class TestCodexReview385(Base):
    """Codex on #385 @051f7af (each failed before the fix)."""

    def test_p1_a_line_twice_never_refunds_what_was_already_returned(self):
        from app.core.errors import Conflict
        from app.sales import estock_returns as er
        from app.sales.cash import CashDrawer
        t = base_tables()
        line = {"sales_id": "1", "details_id": "1", "product_id": "1", "amount": "1", "sell_price": "30.0000",
                "total_sell": "30.0000", "insert_date": SOLD}
        t["Sales_details"] = [line, dict(line)]                           # E-Stock holds the same line twice
        # the base fixture's return document takes back product 1 of invoice 1 (Back_Sales_details back_amount 1)
        self.assertTrue(self.run_import(tables=t)["ok"])
        CashDrawer(self.db).open(self.ctx, 0)
        lines = er.invoice_lines(self.db, self.ctx, "1")
        self.assertEqual(len(lines), 1)                                    # one line, not two
        self.assertEqual(lines[0]["returned_units"], lines[0]["sold_units"])   # E-Stock's return still counts
        with self.assertRaises(Conflict):                                  # nothing left to refund
            er.return_items(self.db, self.ctx, "1", {"1": 1}, reason="مرتجع عميل", key="k1")

    def test_p1_a_line_number_twice_with_other_cells_holds_returns_on_that_invoice(self):
        from app.core.errors import Conflict
        from app.sales import estock_returns as er
        t = base_tables()
        line = {"sales_id": "1", "details_id": "1", "product_id": "1", "amount": "1", "sell_price": "30.0000",
                "total_sell": "30.0000", "insert_date": SOLD}
        t["Sales_details"] = [line, dict(line, amount="2", total_sell="60.0000")]
        t["Back_Sales_details"] = []
        self.assertTrue(self.run_import(tables=t)["ok"])
        with self.assertRaises(Conflict) as e:
            er.return_items(self.db, self.ctx, "1", {"1": 1}, reason="مرتجع عميل", key="k2")
        self.assertEqual(e.exception.code, "estock_history_duplicate")

    def test_p1_a_stale_duplicate_choice_stops_instead_of_dropping_a_row(self):
        t = base_tables()                                                  # pa_id 1 is once in this read
        dec = self.tmp / "packs.json"
        dec.write_text(json.dumps({"duplicates": {"1": 2}}), encoding="utf-8")
        res = self.run_import(tables=t, packs_file=dec)
        self.assertFalse(res["ok"])
        self.assertTrue([b for b in res["plan"].blocking if b["field"] == "duplicates"], res["plan"].blocking)

    def test_p1_a_choice_beyond_the_rows_stops(self):
        t = base_tables()
        t["Product_Amount"].append(stock_row(1, 1, 1, "4", "22.0000", "2027-09-01T00:00:00"))
        dec = self.tmp / "packs.json"
        dec.write_text(json.dumps({"duplicates": {"1": 3}}), encoding="utf-8")
        res = self.run_import(tables=t, packs_file=dec)
        self.assertFalse(res["ok"])


class TestCodexReview385b(Base):
    """Codex on #385 @0f19b3b (failed before the fix)."""

    def test_p1_one_invoice_number_with_two_different_headers_holds_returns(self):
        from app.core.errors import Conflict
        from app.sales import estock_returns as er
        t = base_tables()
        t["Sales_header"][0]["bill_cash"] = "30.0000"
        t["Sales_header"].append(dict(t["Sales_header"][0], bill_cash="0"))     # invoice 1 twice, other payment
        self.assertTrue(self.run_import(tables=t)["ok"])
        with self.assertRaises(Conflict) as e:
            er.return_items(self.db, self.ctx, "1", {"1": 1}, reason="مرتجع عميل", key="k3")
        self.assertEqual(e.exception.code, "estock_history_duplicate")

    def test_an_exact_copy_of_a_header_does_not_hold_returns(self):
        from app.sales import estock_returns as er
        t = base_tables()
        t["Sales_header"].append(dict(t["Sales_header"][0]))                   # invoice 1 twice, the same cells
        self.assertTrue(self.run_import(tables=t)["ok"])
        self.assertTrue(er.invoice_lines(self.db, self.ctx, "1"))



class TestCodexReview385c(Base):
    """Codex on #385 @3266ab9 (failed before the fix): an invoice with two different headers is not added
    to the history reports twice; its rows stay in the archive for review."""

    def test_p2_a_clashing_invoice_is_left_out_of_history_aggregates(self):
        from app.dataimport import estock_history as h
        t = base_tables()
        t["Sales_header"].append(dict(t["Sales_header"][0], bill_cash="0"))     # invoice 1 twice, other cells
        self.assertTrue(self.run_import(tables=t)["ok"])
        got = h.invoices(self.db, self.ctx, "sale")
        self.assertNotIn("1", [r["estock_id"] for r in got])
        cust = self.db.one("SELECT id FROM customers WHERE name LIKE '%أحمد%'")["id"]
        self.assertFalse([r for r in h.customer_items(self.db, self.ctx, cust) if (r.get("qty") or 0) > 1])
