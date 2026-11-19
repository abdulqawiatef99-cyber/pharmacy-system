"""E-Stock database move (F91) on a synthetic export that follows the real schema and its data errors.

Every value here is invented. The layout is estock-db-read.ps1's: tables/<NN>_<instance>/<NN>_<db>/
<NNN>_<schema.table>.csv (UTF-8 with BOM, every cell quoted text) + tables/manifest.csv."""
import csv
import io
import os
import re
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.core.errors import Conflict
from app.dataimport import estock_archive, estock_db
from tests.helpers import DBTestCase, OWNER_PW

ROOT = Path(__file__).resolve().parents[1]
FRESH = "2026-11-18T12:00:00.000"
SOLD = "2026-11-18T11:00:00.000"
OLD = "2025-01-01T10:00:00.000"


def product(pid, code, en, ar="", **kw):
    r = {"product_id": str(pid), "product_code": code, "product_name_en": en, "product_name_ar": ar,
         "product_scientific_name": "", "company_id": "1", "product_drug": "1", "deleted": "0", "active": "1",
         "sell_price": "30.0000", "buy_price": "20.0000", "product_disc1": "0", "product_disc2": "0",
         "product_unit1": "1", "product_unit2": "", "product_unit3": "", "product_unit1_2": "", "product_unit1_3": "",
         "unit2_sell_price": "", "unit3_sell_price": "", "unit2_sell_price_clause": "", "unit3_sell_price_clause": "",
         "product_int_code": "", "product_int_code1": "", "product_int_code2": "", "product_int_code3": "",
         "product_int_code4": ""}
    r.update(kw)
    return r


def stock_row(pa, pid, store, amount, buy, exp, upd=FRESH, branch="3"):
    return {"branch_id": branch, "counter_id": "1", "product_id": str(pid), "store_id": str(store), "vendor_id": "1",
            "amount": amount, "buy_price": buy, "sell_price": "30.0000", "tax_price": "0", "exp_date": exp,
            "insert_uid": "1", "insert_date": OLD, "update_uid": "1", "update_date": upd, "pa_id": str(pa)}


def base_tables():
    t = {}
    t["Companys"] = [{"company_id": "1", "company_code": "1", "co_name_ar": "شركة اختبار", "co_name_en": "Test Co"}]
    t["Stores"] = [{"store_id": "1", "store_code": "1", "store_name_ar": "الصيدليه", "store_name_en": "", "active": "1"},
                   {"store_id": "2", "store_code": "2", "store_name_ar": "مستشفيات الحكمه", "store_name_en": "", "active": "1"}]
    t["Product_units"] = [{"unit_id": "1", "unit_name_ar": "علبة"}, {"unit_id": "2", "unit_name_ar": "شريط"},
                          {"unit_id": "3", "unit_name_ar": "قرص"}]
    t["Products"] = [
        product(1, "100", "Testamol 500 Tab", product_unit2="2", product_unit1_2="3.0000",
                product_int_code="6220000000017", product_int_code1="6220000000024", sell_price="30.0000"),
        product(2, "101", "Testofen Syrup", buy_price="-5.0000", sell_price="25.5000"),
        product(3, "102", "Testaclav 1g Tab", product_unit2="2", product_unit3="3", product_unit1_2="2",
                product_unit1_3="14", product_disc1="-6076", unit2_sell_price_clause="-3.0000", sell_price="140.0000"),
        product(4, "", "", "مرهم تجربة", deleted="1", product_int_code="6220000000017", sell_price="0.0000"),
        product(6, "100", "Testamol Copy Tab"),
    ]
    t["Product_Amount"] = [
        stock_row(1, 1, 1, "10.333333333333334", "24.0000", "2027-05-01T00:00:00"),
        stock_row(2, 1, 1, "0.33600000000000002", "24.0000", "2026-12-01T00:00:00"),
        stock_row(3, 2, 1, "5", "-3.0000", "2028-01-01T00:00:00"),
        stock_row(4, 3, 1, "2.5", "140.0000", "2027-01-01T00:00:00"),
        stock_row(5, 3, 1, "-0.5", "140.0000", "2026-06-01T00:00:00"),
        stock_row(6, 3, 2, "4", "140.0000", "2027-01-01T00:00:00"),
        stock_row(7, 6, 1, "0", "20.0000", "2027-01-01T00:00:00"),
        stock_row(8, 4, 1, "1", "0.0000", "1900-01-01T00:00:00"),
    ]
    t["Branches_Product_Amount"] = [stock_row(1, 1, 1, "50", "24.0000", "2027-05-01T00:00:00", upd=OLD, branch="1")]
    t["Sales_header"] = [
        {"sales_id": "1", "store_id": "1", "customer_id": "1", "total_bill_net": "54.0000", "insert_date": SOLD,
         "ticket_name": "", "ticket_id": "", "ticket_num": ""},
        {"sales_id": "2", "store_id": "1", "customer_id": "1", "total_bill_net": "-10.0000", "insert_date": SOLD,
         "ticket_name": "مستشفى تجربة", "ticket_id": "7", "ticket_num": "T-123"}]
    t["Branches_sales_details"] = [
        {"branch_id": "3", "details_id": "1", "sales_id": "1", "product_id": "1", "amount": "1", "sale_unit_change": "3",
         "sale_unit": "1", "sell_price": "30.0000", "insert_date": SOLD},
        {"branch_id": "3", "details_id": "2", "sales_id": "1", "product_id": "3", "amount": "0.5", "sale_unit_change": "2",
         "sale_unit": "2", "sell_price": "70.0000", "insert_date": SOLD}]
    t["Back_sales_header"] = [{"back_sales_id": "1", "sales_id": "1", "customer_id": "1", "total_bill_net": "30.0000"}]
    t["Back_Sales_details"] = [{"back_sales_id": "1", "sales_id": "1", "product_id": "1", "back_amount": "1"}]
    t["Purchase_header"] = [{"purchase_id": "1", "vendor_id": "1", "total_bill": "500.0000", "bill_date": OLD}]
    t["Purchase_details"] = [{"details_id": "1", "purchase_id": "1", "product_id": "1", "amount": "20", "buy_price": "24.0000"}]
    # E-Stock's signs (summary.txt ranges): a charge is + in one column, a payment - in the other
    t["Gedo_customers"] = [{"gc_id": "1", "customer_id": "1", "gc_for_him": "-30.0000", "gc_for_me": "150.0000",
                            "total": "120.0000"}]
    t["Gedo_Vendors"] = [{"gv_id": "1", "vendor_id": "1", "gv_for_him": "600.0000", "gv_for_me": "-100.0000",
                          "total": "500.0000"}]
    t["Shortcoming"] = [{"id": "1", "product_id": "62212345678901234", "amount": "2", "store_id": "1"}]
    t["Cash_disk_close"] = [{"cdc_id": "1", "cdc_curr_cash": "1000.0000", "cdc_act_cash": "999.5000"}]
    t["Customer"] = [
        {"customer_id": "1", "customer_code": "C1", "customer_name_ar": "أحمد تجربة", "customer_name_en": "Ahmed",
         "mobile": "01012345678", "tel": "0223456789", "address": "شارع التجربة", "customer_current_money": "120.0000",
         "customer_max_money": "9999999999999.0000", "deleted": "0", "active": "1"},
        {"customer_id": "2", "customer_code": "C2", "customer_name_ar": "منى تجربة", "mobile": "01012345678",
         "tel": "12", "customer_current_money": "-15.5000", "customer_max_money": "500.0000", "deleted": "0", "active": "1"},
        {"customer_id": "3", "customer_code": "C3", "customer_name_ar": "", "customer_current_money": "0.0000",
         "customer_max_money": "0", "deleted": "1", "active": "0"}]
    t["Vendor"] = [
        {"vendor_id": "1", "vendor_code": "V1", "vendor_name_ar": "مورد تجربة", "mobile": "01111111111",
         "vendor_current_money": "500.0000", "emp_tel": "مندوب حسن", "emp_tel_details": "01222222222",
         "deleted": "0", "active": "1"},
        {"vendor_id": "2", "vendor_code": "V2", "vendor_name_ar": "مورد تجربه", "vendor_current_money": "-20.0000",
         "deleted": "0", "active": "1"}]
    return t


def write_export(folder: Path, tables: dict) -> Path:
    root = folder / "tables"
    dbdir = root / "01_PC" / "01_stock"
    dbdir.mkdir(parents=True)
    man = []
    for i, (name, rows) in enumerate(tables.items(), start=1):
        cols = list(dict.fromkeys(k for r in rows for k in r)) or ["id"]
        rel = f"01_PC\\01_stock\\{i:03d}_dbo.{name}.csv"
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])
        (dbdir / f"{i:03d}_dbo.{name}.csv").write_bytes(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"))
        man.append({"index": f"01/01/{i:03d}", "instance": "PC", "database": "stock", "schema": "dbo", "table": name,
                    "rows": str(len(rows)), "file": rel})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(man[0]), quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writeheader()
    w.writerows(man)
    (root / "manifest.csv").write_bytes(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"))
    return folder


class Base(DBTestCase):
    def export(self, tables=None, name="exp"):
        return write_export(Path(tempfile.mkdtemp(dir=self.tmp, prefix=name)), tables or base_tables())

    def run_import(self, tables=None, folder=None, write_archive=True, **kw):
        folder = folder or self.export(tables)
        self._last = folder
        return estock_db.run(self.db, self.ctx, export_dir=folder, out_dir=self.tmp / "reports",
                             archive_path=self.tmp / estock_archive.NAME, write_archive=write_archive, **kw)

    def dq(self, res):
        return res["plan"].dq

    def item(self, code):
        return self.db.one("SELECT * FROM items WHERE code=?", (code,))

    def units(self, code):
        """The pharmacy's own units of the item (with 0039 a hospital-owned batch is not the pharmacy's)."""
        own = " AND coalesce(b.owner,'pharmacy')='pharmacy'" if any(
            r["name"] == "owner" for r in self.db.all("PRAGMA table_info(batches)")) else ""
        return self.db.one("SELECT coalesce(sum(m.qty_delta),0) q FROM stock_movements m LEFT JOIN batches b ON"
                           f" b.id=m.batch_id WHERE m.item_id=?{own}", (self.item(code)["id"],))["q"]


class TestFullMove(Base):
    def test_moves_items_stock_parties_and_reconciles(self):
        res = self.run_import()
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        # items: codes, duplicate and empty codes get E<id>, packs from the unit conversions
        self.assertEqual({r["code"] for r in self.db.all("SELECT code FROM items")}, {"100", "101", "102", "E4", "E6"})
        self.assertEqual((self.item("100")["pack_size"], self.item("102")["pack_size"], self.item("101")["pack_size"]),
                         (3, 14, 1))
        self.assertEqual(self.item("100")["public_price"], 3000)
        self.assertIsNone(self.item("E4")["public_price"])
        self.assertEqual(self.item("E4")["status"], "inactive")
        self.assertEqual(self.item("100")["manufacturer"], "شركة اختبار")
        bcs = {r["barcode"]: r["item_id"] for r in self.db.all("SELECT * FROM item_barcodes")}
        self.assertEqual(bcs, {"6220000000017": self.item("100")["id"], "6220000000024": self.item("100")["id"]})
        # stock: drift rounded, negative row netted, the hospital store left out, float noise ignored
        self.assertEqual((self.units("100"), self.units("101"), self.units("102"), self.units("E4")), (32, 5, 28, 1))
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements WHERE item_id=?", (self.item("E6")["id"],)))
        cost = self.db.one("SELECT unit_cost FROM stock_movements WHERE item_id=?", (self.item("101")["id"],))
        self.assertIsNone(cost["unit_cost"])                      # negative E-Stock cost: posted without a cost
        exp = {r["expiry_date"] for r in self.db.all("SELECT expiry_date FROM batches WHERE item_id=?",
                                                     (self.item("100")["id"],))}
        self.assertEqual(exp, {"2027-05-01", "2026-12-01"})
        b = self.db.one("SELECT b.batch_no, b.expiry_date, b.public_price FROM stock_movements m JOIN batches b"
                        " ON b.id=m.batch_id WHERE m.item_id=?", (self.item("E4")["id"],))
        self.assertEqual((b["batch_no"], b["expiry_date"]), ("1-1", None))   # E-Stock's batch key, no expiry
        self.assertEqual(self.db.one("SELECT public_price FROM batches WHERE batch_no='1-1' AND item_id=?",
                                     (self.item("100")["id"],))["public_price"], 3000)
        self.assertEqual(res["plan"].stock_source["decision"]["key"], "Product_Amount:3")
        st = res["reconciliation"]["stock"]
        self.assertEqual((st["planned_units"], st["program_units"]), (66, 66))
        self.assertEqual(st["program_cost"], st["planned_cost"])
        # parties and balances
        c = {r["name"]: r for r in self.db.all("SELECT * FROM customers")}
        self.assertEqual(set(c), {"أحمد تجربة", "منى تجربة", "عميل C3"})
        self.assertEqual(c["أحمد تجربة"]["phone"], "01012345678")
        self.assertEqual(c["أحمد تجربة"]["phone2"], "0223456789")
        self.assertIsNone(c["منى تجربة"]["phone"])                # same number as Ahmed: kept in the notes
        self.assertIn("01012345678", c["منى تجربة"]["notes"])
        # E-Stock's 9,999,999,999,999 = no limit there: kept as-is, never 0 (0 = no credit at all here)
        self.assertEqual((c["أحمد تجربة"]["credit_limit"], c["منى تجربة"]["credit_limit"]), (999999999999900, 50000))
        self.assertEqual(c["عميل C3"]["is_active"], 0)
        bal = {r["customer_id"]: (r["amount"], r["kind"]) for r in self.db.all("SELECT * FROM customer_ledger")}
        self.assertEqual(bal, {c["أحمد تجربة"]["id"]: (12000, "adjustment"), c["منى تجربة"]["id"]: (-1550, "adjustment")})
        s = {r["name"]: r for r in self.db.all("SELECT * FROM suppliers")}
        self.assertEqual(set(s), {"مورد تجربة", "مورد تجربه (V2)"})
        sl = sorted((r["amount"], r["kind"]) for r in self.db.all("SELECT * FROM supplier_ledger"))
        self.assertEqual(sl, [(-2000, "opening"), (50000, "opening")])
        self.assertEqual(res["reconciliation"]["customers"]["program_balance_total"], "104.50")
        # import runs: the launch-night guards see an applied opening stock
        kinds = {r["kind"] for r in self.db.all("SELECT kind FROM import_runs WHERE status='applied'")}
        self.assertEqual(kinds, {"items", "suppliers", "customers", "opening_stock"})

    def test_data_quality_lists_every_impossible_value(self):
        res = self.run_import()
        got = {(d["code"], d["field"]) for d in self.dq(res)}
        for want in (("102", "product_disc1"), ("102", "unit2_sell_price_clause"), ("101", "buy_price"),
                     ("E4", "product_int_code"), ("", "product_code"), ("100", "product_code"),
                     ("100", "amount"), ("102", "amount"), ("E4", "exp_date"), ("C1", "customer_max_money"),
                     ("C2", "mobile/tel"), ("V2", "vendor_name"), ("62212345678901234", "product_id"),
                     ("", "total_bill_net")):
            self.assertIn(want, got)
        self.assertFalse(res["plan"].blocking)
        text = Path(res["reports"]["data_quality"]).read_text(encoding="utf-8-sig")
        self.assertIn("-6076", text)
        self.assertIn("9999999999999.0000", text)
        rec = Path(res["reports"]["reconciliation"]).read_text(encoding="utf-8-sig")
        self.assertTrue(rec.startswith("مطابقة نقل إي ستوك — OK"))
        self.assertIn("Product_Amount:3", rec)
        contacts = Path(res["reports"]["supplier_contacts"]).read_text(encoding="utf-8-sig")
        self.assertIn("مندوب حسن", contacts)

    def test_history_goes_to_a_read_only_archive(self):
        res = self.run_import()
        path = self.tmp / estock_archive.NAME
        self.assertFalse(os.stat(path).st_mode & stat.S_IWUSR)
        con = estock_archive.open_ro(path)
        try:
            self.assertEqual(con.execute('SELECT ticket_num FROM "e_Sales_header" WHERE sales_id=?', ("2",)).fetchone()[0],
                             "T-123")
            self.assertEqual(con.execute('SELECT count(*) FROM "e_Branches_sales_details"').fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT product_id FROM "e_Shortcoming"').fetchone()[0], "62212345678901234")
            cid = con.execute("SELECT program_id FROM map_customers WHERE estock_id='1'").fetchone()[0]
            self.assertEqual(self.db.one("SELECT name FROM customers WHERE id=?", (cid,))["name"], "أحمد تجربة")
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM meta")
        finally:
            con.close()
        h = res["reconciliation"]["history"]
        self.assertEqual(h["Sales_header"], {"manifest": 2, "csv": 2, "archive": 2, "quarantined": 0})
        self.assertEqual(h["Gedo_customers"]["archive"], 1)

    def test_same_export_again_resumes_and_writes_nothing_twice(self):
        folder = self.export()
        self.assertTrue(self.run_import(folder=folder)["ok"])
        n = {t: self.db.one(f"SELECT count(*) n FROM {t}")["n"] for t in
             ("items", "stock_movements", "customer_ledger", "supplier_ledger", "customers", "suppliers")}
        res = self.run_import(folder=folder)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertEqual({t: self.db.one(f"SELECT count(*) n FROM {t}")["n"] for t in n}, n)

    def test_resume_after_a_failed_stock_step(self):
        folder = self.export()
        from app.inventory import service as inv
        with mock.patch.object(inv, "post_movement", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                self.run_import(folder=folder)
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], 5)
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements"))
        res = self.run_import(folder=folder)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], 5)
        self.assertEqual(self.units("100"), 32)


class TestReconciliation(Base):
    def test_big_split_item_reconciles_to_the_piaster(self):
        t = base_tables()
        t["Products"].append(product(7, "105", "Testbig Tab", product_unit3="3", product_unit1_3="3"))
        t["Product_Amount"].append(stock_row(13, 7, 1, "4000", "12.5000", "2027-01-01T00:00:00"))
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertEqual(self.units("105"), 12000)

    def test_a_difference_from_the_plan_is_reported_biggest_first(self):
        folder = self.export()
        res = self.run_import(folder=folder)
        plan = res["plan"]
        plan.per_item["1"]["planned_units"] += 1
        plan.per_item["3"]["planned_cost"] += Decimal("100")
        rec = estock_db.reconcile(self.db, self.ctx, plan, res["runs"], estock_db.Export(folder), None)
        self.assertFalse(rec["ok"])
        self.assertEqual([m["code"] for m in rec["mismatches"]], ["102", "100"])


class TestOwnerConfirmedBalances(Base):
    def test_confirmed_amount_wins_and_the_difference_is_an_adjustment(self):
        from openpyxl import load_workbook
        first = self.run_import(write_archive=False, folder=self.export())
        sheet = Path(first["reports"]["openings"])
        wb = load_workbook(sheet)
        ws = wb.active
        head = [c.value for c in ws[1]]
        self.assertEqual(head, estock_db.SHEET_HEAD)
        names = [r[3].value for r in ws.iter_rows(min_row=2)]
        self.assertEqual(names[0], "مورد تجربة")                     # biggest balance first
        for r in ws.iter_rows(min_row=2):
            if r[1].value == "1" and r[0].value == "مورد":
                r[5].value = 450
            if r[1].value == "1" and r[0].value == "عميل":
                r[7].value = 1000
        conf = self.tmp / "confirmed.xlsx"
        wb.save(conf)
        # a fresh database, as on launch night (the dry run wrote into this one)
        self.db.close()
        DBTestCase.setUp(self)
        res = self.run_import(confirm_file=conf)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        v1 = self.db.one("SELECT id FROM suppliers WHERE name='مورد تجربة'")["id"]
        rows = sorted((r["amount"], r["kind"]) for r in self.db.all("SELECT * FROM supplier_ledger WHERE supplier_id=?", (v1,)))
        self.assertEqual(rows, [(-5000, "adjustment"), (50000, "opening")])
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_ledger WHERE kind='payment'"))
        self.assertFalse(self.db.one("SELECT 1 FROM customer_ledger WHERE kind='payment'"))
        self.assertEqual(self.db.one("SELECT credit_limit FROM customers WHERE name='أحمد تجربة'")["credit_limit"], 100000)
        s = res["reconciliation"]["suppliers"]
        self.assertEqual((s["estock_balance_total"], s["confirmed_balance_total"], s["opening_adjustment_total"],
                          s["program_balance_total"]), ("480.00", "430.00", "-50.00", "430.00"))
        # another confirmation file afterwards is refused (adjustments are never doubled)
        ws2 = load_workbook(conf)
        for r in ws2.active.iter_rows(min_row=2):
            if r[1].value == "1" and r[0].value == "مورد":
                r[5].value = 400
        conf2 = self.tmp / "confirmed2.xlsx"
        ws2.save(conf2)
        with self.assertRaises(Conflict):
            self.run_import(confirm_file=conf2, folder=self.export())


class TestStaleConfirmation(Base):
    def test_a_balance_that_changed_after_the_owner_confirmed_it_blocks(self):
        from openpyxl import load_workbook
        first = self.run_import(write_archive=False)
        wb = load_workbook(first["reports"]["openings"])
        for r in wb.active.iter_rows(min_row=2):
            if r[1].value == "1" and r[0].value == "مورد":
                r[5].value = 450
        conf = self.tmp / "confirmed.xlsx"
        wb.save(conf)
        self.db.close()
        DBTestCase.setUp(self)
        t = base_tables()
        t["Vendor"][0]["vendor_current_money"] = "700.0000"      # a purchase after the rehearsal
        res = self.run_import(tables=t, confirm_file=conf)
        self.assertFalse(res["ok"])
        self.assertIn(("V1", "confirmed balance"), {(b["code"], b["field"]) for b in res["plan"].blocking})
        self.assertFalse(self.db.one("SELECT 1 FROM suppliers"))


class TestRefusalsAndDecisions(Base):
    def test_refuses_a_database_with_sales_or_stock(self):
        from app.catalog import service as cat
        from app.inventory import service as inv
        item = cat.create_item(self.db, self.ctx, {"name_en": "Old", "pack_size": 1})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=2, movement_type="opening", unit_cost=Decimal(1))
        with self.assertRaises(Conflict) as e:
            self.run_import()
        self.assertIn("حركات مخزون", e.exception.message)

    def test_refuses_another_export_after_a_move(self):
        self.assertTrue(self.run_import()["ok"])
        t = base_tables()
        t["Vendor"][0]["mobile"] = "01133333333"
        with self.assertRaises(Conflict):
            self.run_import(tables=t)

    def test_unclear_live_stock_table_stops_and_explicit_choice_works(self):
        t = base_tables()                                   # no Product_Amount: two fresh branch groups
        t["Branches_Product_Amount"] = [dict(r, branch_id="3") for r in t.pop("Product_Amount")] + \
            [stock_row(9, 1, 1, "50", "24.0000", "2027-05-01T00:00:00", branch="1"),
             stock_row(10, 3, 1, "1", "140.0000", "2027-05-01T00:00:00", branch="1")]
        folder = self.export(t)
        res = self.run_import(folder=folder)
        self.assertFalse(res["ok"])
        self.assertFalse(res["applied"])
        self.assertFalse(self.db.one("SELECT 1 FROM items"))
        self.assertIn("stock_source", {b["field"] for b in res["plan"].blocking})
        res = self.run_import(folder=folder, stock_source="Branches_Product_Amount:3")
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])

    def test_broken_unit_conversion_needs_a_pack_decision(self):
        t = base_tables()
        t["Products"].append(product(5, "104", "Testbroken Tab", product_unit3="3", product_unit1_3="2.5"))
        t["Product_Amount"].append(stock_row(11, 5, 1, "2", "10.0000", "2027-01-01T00:00:00"))
        folder = self.export(t)
        res = self.run_import(folder=folder)
        self.assertFalse(res["ok"])
        self.assertFalse(self.db.one("SELECT 1 FROM items"))
        packs = self.tmp / "packs.json"
        packs.write_text('{"104": 10}', encoding="utf-8")
        res = self.run_import(folder=folder, packs_file=packs)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertEqual(self.units("104"), 20)

    def test_fraction_that_does_not_fit_the_pack_blocks(self):
        t = base_tables()
        t["Product_Amount"].append(stock_row(12, 2, 1, "1.5", "10.0000", "2027-01-01T00:00:00"))
        res = self.run_import(tables=t)
        self.assertFalse(res["ok"])
        self.assertIn(("101", "amount"), {(b["code"], b["field"]) for b in res["plan"].blocking})

    def test_opposite_balance_sign_in_the_statements_blocks(self):
        t = base_tables()
        t["Gedo_customers"] = [{"gc_id": "1", "customer_id": "1", "gc_for_him": "30.0000", "gc_for_me": "-150.0000",
                                "total": "-120.0000"}]
        res = self.run_import(tables=t)
        self.assertFalse(res["ok"])
        self.assertIn("balance sign", {b["field"] for b in res["plan"].blocking})

    def test_archive_from_another_export_is_refused(self):
        self.assertTrue(self.run_import()["ok"])
        archive = self.tmp / estock_archive.NAME
        self.db.close()
        DBTestCase.setUp(self)            # a new, empty database; the old archive is still there
        t = base_tables()
        t["Vendor"][0]["mobile"] = "01133333333"
        with self.assertRaises(Conflict):
            estock_db.run(self.db, self.ctx, export_dir=self.export(t), out_dir=self.tmp / "r",
                          archive_path=archive, write_archive=True)
        self.assertFalse(self.db.one("SELECT 1 FROM items"))


class TestCommand(Base):
    def cmd(self, folder, apply, **kw):
        import manage
        s = SimpleNamespace(data_dir=self.tmp / "data", upload_dir=self.tmp / "data" / "uploads",
                            backup_path=self.tmp / "backups")
        s.data_dir.mkdir(exist_ok=True)
        a = SimpleNamespace(username="owner", password=OWNER_PW, dir=str(folder), confirm=None, packs=None, status=False,
                            stock_source=None, store=None, database=None, out=str(self.tmp / ("o" + str(apply))),
                            apply=apply, backup_dir=None, **kw)
        buf = io.StringIO()
        with mock.patch.object(manage, "_db", return_value=self.db), redirect_stdout(buf):
            try:
                manage.cmd_import_estock_db(a, s)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue(), s

    def test_dry_run_writes_nothing_then_apply_backs_up_first(self):
        folder = self.export()
        code, out, s = self.cmd(folder, False)
        self.assertEqual(code, 0, out)
        self.assertIn("RECONCILIATION: OK", out)
        self.assertFalse(self.db.one("SELECT 1 FROM items"))
        self.assertFalse((s.data_dir / estock_archive.NAME).exists())
        self.assertTrue((self.tmp / "oFalse" / "openings-to-confirm.xlsx").exists())
        code, out, s = self.cmd(folder, True)
        self.assertEqual(code, 0, out)
        self.assertIn("RECONCILIATION: OK", out)
        self.assertTrue(list(s.backup_path.glob("pharmacy-backup-*-pre-estock-db.zip")))
        self.assertTrue(list(s.backup_path.glob("estock-archive-*.sqlite")))
        # beside the database the backup reads (here outside the data folder, as with PHARM_DB_PATH; #339 review)
        self.assertTrue((Path(self.db.path).parent / estock_archive.NAME).exists())
        self.assertFalse((s.data_dir / estock_archive.NAME).exists())
        from app.core import backup
        import zipfile
        with zipfile.ZipFile(backup.create(self.db, s.upload_dir, self.tmp / "bk2")) as z:
            self.assertIn(backup.ARCHIVE_MEMBER, z.namelist())
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], 5)

    def test_failed_reconciliation_exits_non_zero(self):
        t = base_tables()
        t["Product_Amount"].append(stock_row(12, 2, 1, "1.5", "10.0000", "2027-01-01T00:00:00"))
        code, out, _ = self.cmd(self.export(t), False)
        self.assertEqual(code, 1)
        self.assertIn("RECONCILIATION: FAILED", out)


BAT = ROOT / "scripts" / "windows" / "estock-db-import.bat"


class TestBat(unittest.TestCase):
    def setUp(self):
        raw = BAT.read_bytes()
        self.raw, self.text = raw, raw.decode("ascii")
        self.lines = [l.strip() for l in self.text.splitlines() if l.strip() and not l.strip().upper().startswith("REM")]

    def test_crlf_and_no_delayed_expansion(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))
        self.assertNotIn("EnableDelayedExpansion", self.text)

    def test_dry_run_confirm_apply_verify_in_order(self):
        L = self.lines
        main = L[:L.index(":rehearsal")]                   # the rehearsal (exit 4, trial data) has its own run below
        runs = [i for i, l in enumerate(main) if '"%MANAGE%" import-estock-db' in l and "--status" not in l]
        self.assertEqual(len(runs), 2)
        rehearsal = [l for l in L[L.index(":rehearsal"):] if '"%MANAGE%" import-estock-db' in l]
        self.assertEqual(len(rehearsal), 1)
        self.assertIn("--rehearsal", rehearsal[0])
        self.assertNotIn("--apply", rehearsal[0])
        dry, apply = runs
        self.assertNotIn("--apply", L[dry])
        self.assertIn("--apply", L[apply])
        stop = next(i for i, l in enumerate(L) if l == "call :stopprog")
        confirm = next(i for i, l in enumerate(L) if 'set /p "ANS=Type YES' in l)
        check = next(i for i, l in enumerate(L) if '"%MANAGE%" verify' in l)
        self.assertTrue(stop < dry < confirm < apply < check)
        for at in (dry, apply):
            block = " ".join(L[at:at + 7])
            self.assertIn('if not "%RC%"=="0"', block)
            self.assertIn('findstr /c:"RECONCILIATION: OK"', block)

    def test_flags_exist_and_only_own_processes_stop(self):
        src = (ROOT / "manage.py").read_text(encoding="utf-8")
        for flag in set(re.findall(r" (--[a-z-]+)", " ".join(l for l in self.lines if "%MANAGE%" in l))):
            self.assertIn(f'"{flag}"', src, flag)
        self.assertNotIn("taskkill", self.text.lower())
        kills = [l for l in self.lines if "Stop-Process" in l]
        self.assertEqual(len(kills), 1)
        self.assertIn("$_.ExecutablePath.ToLower().StartsWith($d)", kills[0])
        for l in self.lines:
            if "powershell" in l:
                self.assertIn("<nul", l)

    def test_every_failure_is_logged(self):
        L = self.lines
        for l in L[:L.index(":fail")]:
            if l == 'if "%RC%"=="4" goto rehearsal':      # not a failure: the database holds trial data (#362)
                continue
            if l.lower().startswith("if ") and "goto" in l:
                self.assertRegex(l, r'set "MSG=.+" & goto fail\)$')
        self.assertIn('call :log FAIL "%MSG%"', L)


if __name__ == "__main__":
    unittest.main()


class TestCodexReview339(Base):
    """Codex on #339 @3987571: one test per finding (each failed before the fix)."""

    def test_p1_negative_fraction_needs_a_pack_decision_not_rounding(self):
        t = base_tables()
        t["Product_Amount"] += [stock_row(20, 2, 1, "10", "5.0000", "2027-01-01T00:00:00"),
                                stock_row(21, 2, 1, "-0.5", "5.0000", "2027-02-01T00:00:00"),
                                stock_row(22, 2, 1, "-0.5", "5.0000", "2027-03-01T00:00:00")]
        res = self.run_import(tables=t)
        self.assertFalse(res["ok"])
        self.assertIn(("101", "amount"), {(b["code"], b["field"]) for b in res["plan"].blocking})
        self.assertFalse(self.db.one("SELECT 1 FROM stock_movements"))

    def test_p1_sub_piastre_balance_blocks(self):
        t = base_tables()
        t["Customer"][0]["customer_current_money"] = "120.0050"
        t["Gedo_customers"] = []
        res = self.run_import(tables=t, round_piastres=False)          # --no-round-piastres
        self.assertFalse(res["ok"])
        self.assertIn(("C1", "customer_current_money"), {(b["code"], b["field"]) for b in res["plan"].blocking})
        self.assertFalse(self.db.one("SELECT 1 FROM customer_ledger"))

    def test_sub_piastre_balance_rounds_half_up_by_default_listed_with_totals(self):
        """Owner 19/11: half up to the piaster, one line per value with its difference, totals reported."""
        t = base_tables()
        t["Vendor"][0]["vendor_current_money"] = "675493.1710"
        t["Gedo_Vendors"] = []
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        row = [d for d in res["plan"].dq if d["field"] == "vendor_current_money"][0]
        self.assertEqual(row["severity"], "fixed")
        self.assertIn("-0.001", row["action"])
        self.assertEqual(self.db.one("SELECT amount FROM supplier_ledger WHERE amount>0")["amount"], 67549317)
        self.assertEqual(res["plan"].checks["rounding"]["Vendor"]["values"], 1)
        self.assertIn("تقريب كسور القرش", Path(res["reports"]["reconciliation"]).read_text(encoding="utf-8-sig"))
        self.db.close()
        Base.setUp(self)
        res = self.run_import(tables=t, round_piastres=False)
        self.assertIn("vendor_current_money", {d["field"] for d in res["plan"].blocking})

    def test_p1_sub_piastre_price_is_listed_not_silent(self):
        t = base_tables()
        t["Products"][0]["sell_price"] = "30.0050"
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertIn(("100", "sell_price"), {(d["code"], d["field"]) for d in res["plan"].dq})

    def test_p1_sub_piastre_confirmed_amount_is_refused(self):
        from openpyxl import load_workbook
        from app.core.errors import ValidationFailed
        first = self.run_import(write_archive=False)
        wb = load_workbook(first["reports"]["openings"])
        for r in wb.active.iter_rows(min_row=2):
            if r[1].value == "1" and r[0].value == "مورد":
                r[5].value = "450.005"
        conf = self.tmp / "c.xlsx"
        wb.save(conf)
        with self.assertRaises(ValidationFailed):
            estock_db.read_confirm(conf)

    def test_p2_cancelling_rows_reconcile(self):
        t = base_tables()
        t["Products"].append(product(8, "106", "Testcancel Tab", product_unit2="2", product_unit1_2="3"))
        t["Product_Amount"] += [stock_row(30, 8, 1, "10000", "12.5000", "2027-01-01T00:00:00"),
                                stock_row(31, 8, 1, "-10000", "12.5000", "2026-01-01T00:00:00")]
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])

    def test_p2_resume_refused_after_a_customer_payment(self):
        from openpyxl import load_workbook
        from app.sales.service import Sales
        folder = self.export()
        first = self.run_import(folder=folder)
        self.assertTrue(first["ok"])
        cid = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]
        Sales(self.db).customer_payment(self.ctx, cid, 2000, method="card")
        wb = load_workbook(first["reports"]["openings"])
        for r in wb.active.iter_rows(min_row=2):
            if r[1].value == "1" and r[0].value == "عميل":
                r[5].value = 110
        conf = self.tmp / "c.xlsx"
        wb.save(conf)
        n = self.db.one("SELECT count(*) n FROM customer_ledger")["n"]
        with self.assertRaises(Conflict):
            self.run_import(folder=folder, confirm_file=conf)
        self.assertEqual(self.db.one("SELECT count(*) n FROM customer_ledger")["n"], n)


class TestArchiveInBackup(Base):
    """Owner 18/11: the E-Stock history is owner data and goes into every regular backup."""

    def test_backup_carries_verifies_and_restores_the_archive(self):
        import zipfile
        from app.core import backup
        self.assertTrue(self.run_import()["ok"])
        arch = self.tmp / estock_archive.NAME
        out = backup.create(self.db, self.tmp / "uploads", self.tmp / "bk")
        with zipfile.ZipFile(out) as z:
            self.assertIn(backup.ARCHIVE_MEMBER, z.namelist())
        self.assertEqual(backup.verify_archive(out)["problems"], [])
        self.assertEqual(backup.quick_check(out), [])
        again = backup.create(self.db, self.tmp / "uploads", self.tmp / "bk")      # cached .xz, same content
        self.assertEqual(backup.verify_archive(again)["problems"], [])
        target = self.tmp / "restored"
        backup.restore(out, target / "pharmacy.db", target / "uploads", self.tmp / "pre")
        got = target / estock_archive.NAME
        self.assertEqual(got.read_bytes(), arch.read_bytes())
        self.assertFalse(os.stat(got).st_mode & stat.S_IWUSR)

    def test_a_tampered_or_missing_archive_fails_verification(self):
        import zipfile
        from app.core import backup
        self.assertTrue(self.run_import()["ok"])
        out = backup.create(self.db, self.tmp / "uploads", self.tmp / "bk")
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(out) as src, zipfile.ZipFile(bad, "w") as dst:
            for i in src.infolist():
                if i.filename != backup.ARCHIVE_MEMBER:
                    dst.writestr(i, src.read(i.filename))
        self.assertTrue(backup.verify_archive(bad)["problems"])
        with self.assertRaises(RuntimeError):
            backup.restore(bad, self.tmp / "r" / "pharmacy.db", self.tmp / "r" / "uploads", self.tmp / "pre")

    def test_restoring_an_older_backup_keeps_the_live_archive_aside(self):
        from app.core import backup
        old = backup.create(self.db, self.tmp / "uploads", self.tmp / "bk")          # before the move
        self.assertTrue(self.run_import()["ok"])
        live = Path(self.db.path)
        self.db.close()
        backup.restore(old, live, self.tmp / "uploads", self.tmp / "pre")
        self.assertFalse((self.tmp / estock_archive.NAME).exists())
        self.assertEqual(len(list(self.tmp.glob("estock-archive-replaced-*.sqlite"))), 1)
        from app.db import Database
        self.db = Database(live)


class TestOpeningBalancesInTheProgram(Base):
    """Owner 18/11: the owner says each real balance; it is confirmed from the program, not a sheet."""

    def setUp(self):
        super().setUp()
        self.folder = self.export()
        self.assertTrue(self.run_import(folder=self.folder)["ok"])
        from app.dataimport import opening_balances as ob
        self.ob = ob
        self.v1 = self.db.one("SELECT id FROM suppliers WHERE name='مورد تجربة'")["id"]
        self.c1 = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]

    def test_listing_and_confirm_post_an_opening_adjustment_only(self):
        rows = self.ob.listing(self.db, self.ctx, "supplier")
        self.assertEqual([(r["name"], r["estock_opening"], r["opening"]) for r in rows],
                         [("مورد تجربة", 50000, 50000), ("مورد تجربه (V2)", -2000, -2000)])
        self.assertEqual(rows[0]["estock_code"], "V1")
        got = self.ob.confirm(self.db, self.ctx, "supplier", self.v1, 45000, note="قال المالك")
        self.assertEqual((got["estock_opening"], got["opening_adjustments"], got["opening"], got["balance_now"]),
                         (50000, -5000, 45000, 45000))
        led = self.db.all("SELECT amount, kind, note FROM supplier_ledger WHERE supplier_id=? ORDER BY amount", (self.v1,))
        self.assertEqual([(r["amount"], r["kind"]) for r in led], [(-5000, "adjustment"), (50000, "opening")])
        self.assertIn("تسوية افتتاحية", led[0]["note"])
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_ledger WHERE kind='payment'"))
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='estock.opening_confirm'"))
        self.ob.confirm(self.db, self.ctx, "supplier", self.v1, 45000)                  # same figure: nothing new
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_ledger WHERE supplier_id=?", (self.v1,))["n"], 2)

    def test_later_activity_does_not_change_the_opening(self):
        from app.sales.service import Sales
        Sales(self.db).customer_payment(self.ctx, self.c1, 2000, method="card")
        got = self.ob.confirm(self.db, self.ctx, "customer", self.c1, 11000)
        self.assertEqual((got["opening"], got["opening_adjustments"], got["balance_now"]), (11000, -1000, 9000))

    def test_owner_only_and_refusals(self):
        from app.core import people
        from app.core.errors import Forbidden, NotFound, ValidationFailed
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password="Staff-Pass-2026", role_code="manager")
        mgr = self.login("mgr", "Staff-Pass-2026")
        for call in (lambda: self.ob.listing(self.db, mgr, "supplier"),
                     lambda: self.ob.confirm(self.db, mgr, "supplier", self.v1, 1)):
            with self.assertRaises(Forbidden):
                call()
        for bad in (True, 12.5, "100", 10 ** 12):
            with self.assertRaises(ValidationFailed):
                self.ob.confirm(self.db, self.ctx, "supplier", self.v1, bad)
        with self.assertRaises(ValidationFailed):
            self.ob.listing(self.db, self.ctx, "bank")
        from app.catalog import service as cat
        other = cat.create_supplier(self.db, self.ctx, name="مورد جديد")
        with self.assertRaises(NotFound):
            self.ob.confirm(self.db, self.ctx, "supplier", other, 100)

    def test_a_confirmation_without_a_difference_also_closes_the_import(self):
        """#339 review: confirming the same figure posts no ledger row, but no import step may run after it."""
        self.ob.confirm(self.db, self.ctx, "supplier", self.v1, 50000)                # = E-Stock: no row
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_ledger WHERE supplier_id=?", (self.v1,))["n"], 1)
        with self.assertRaises(Conflict):
            self.run_import(folder=self.folder)

    def test_no_import_step_runs_after_a_confirmation_in_the_program(self):
        self.ob.confirm(self.db, self.ctx, "supplier", self.v1, 45000)
        with self.assertRaises(Conflict):
            self.run_import(folder=self.folder)


class TestStatementSigns(Base):
    """Manager 22:15 (summary.txt ranges): balance = sum(for_him + for_me), and the last running total."""

    def test_realistic_signs_match_the_balance(self):
        res = self.run_import()
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        sign = res["plan"].checks["balance_sign"]
        self.assertEqual((sign["customers"]["statement_matches"], sign["suppliers"]["statement_matches"]), (1, 1))

    def test_the_last_running_total_counts_when_the_lines_do_not_add_up(self):
        t = base_tables()
        t["Gedo_customers"] = [{"gc_id": "1", "customer_id": "1", "gc_for_him": "0", "gc_for_me": "999.0000",
                                "total": "120.0000", "insert_date": "2026-11-01T10:00:00"}]
        res = self.run_import(tables=t)
        self.assertEqual(res["plan"].checks["balance_sign"]["customers"]["last_total_matches"], 1)

    def test_one_odd_party_does_not_block(self):
        t = base_tables()
        t["Customer"].append({"customer_id": "4", "customer_code": "C4", "customer_name_ar": "عميل رابع",
                              "customer_current_money": "50.0000"})
        t["Customer"].append({"customer_id": "5", "customer_code": "C5", "customer_name_ar": "عميل خامس",
                              "customer_current_money": "70.0000"})
        t["Gedo_customers"] += [{"gc_id": "2", "customer_id": "4", "gc_for_him": "0", "gc_for_me": "-50.0000"},
                                {"gc_id": "3", "customer_id": "5", "gc_for_him": "0", "gc_for_me": "70.0000"}]
        res = self.run_import(tables=t)                 # 2 of 3 match, 1 negated: not most of them
        self.assertNotIn("balance sign", {b["field"] for b in res["plan"].blocking})


class TestOwnerDecisions1118(Base):
    def test_no_expiry_is_split_into_drug_to_review_and_non_drug(self):
        t = base_tables()
        t["Products"].append(product(9, "107", "Testcream", product_drug="0"))
        t["Product_Amount"] += [stock_row(40, 9, 1, "3", "5.0000", ""), stock_row(41, 2, 1, "2", "5.0000", "")]
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        exp = {(d["code"], d["severity"]) for d in res["plan"].dq if d["field"] == "expiry"}
        self.assertIn(("107", "info"), exp)
        self.assertIn(("101", "review"), exp)
        self.assertIn("للمراجعة", Path(res["reports"]["data_quality"]).read_text(encoding="utf-8-sig"))

    def test_treasury_and_wallets_are_found_with_their_source_and_kept_pending(self):
        """Owner 22:05: they move (F105 posts them); until then every figure is in the report, none dropped."""
        t = base_tables()
        t["Gedo_Financial"] = [{"gf_id": "1", "fcs_id": "1", "gf_for_me": "1500.5000", "gf_for_him": "200.0000"},
                               {"gf_id": "2", "fcs_id": "2", "gf_for_me": "300.0000", "gf_for_him": "0.0000"}]
        t["Financial_cash_safe"] = [{"fcs_id": "1", "fcs_name": "الخزنة الرئيسية"}, {"fcs_id": "2", "fcs_name": "فودافون كاش"}]
        t["Networks"] = [{"network_id": "2", "network_name": "إنستاباي"}]
        t["Cash_disk_close"][0].update({"cdc_fcs_id": "1", "cdc_trans_value": "900.0000"})
        t["Sales_header"][0]["network_id"] = "2"
        t["Sales_header"][0]["network_money"] = "54.0000"
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        mi = res["plan"].checks["money_info"]
        gf = {x["table"]: x for x in mi["tables"]}["Gedo_Financial"]
        self.assertEqual(gf["sums"]["gf_for_me"], "1800.50")
        self.assertEqual([(g["id"], g["name"], g["gf_for_me"]) for g in gf["by"]["fcs_id"]],
                         [("1", "الخزنة الرئيسية", "1500.50"), ("2", "فودافون كاش", "300.00")])
        self.assertEqual(mi["shift_transfers"][0]["total"], "900.00")
        self.assertEqual(mi["last_shift_close"][0]["counted_cash"], "999.50")
        self.assertEqual([(n["network_id"], n["name"], n["total"]) for n in mi["sales_by_network"]], [("2", "إنستاباي", "54.00")])
        review = {(d["table"], d["field"]) for d in res["plan"].dq if d["severity"] == "review"}
        self.assertIn(("Gedo_Financial", "gf_for_me"), review)
        self.assertIn(("Cash_disk_close", "cdc_trans_value"), review)
        for table in ("cash_movements", "cash_shifts"):          # posted only by F105's service, once
            self.assertFalse(self.db.one(f"SELECT 1 FROM {table}"), table)
        rec = Path(res["reports"]["reconciliation"]).read_text(encoding="utf-8-sig")
        self.assertIn("MONEY OPENING: PENDING", rec)
        self.assertIn("الخزنة الرئيسية", rec)


class TestPartialReadIsCompletedFirst(Base):
    """Manager 18/11: a PARTIAL read is completed by reading only the failed tables again, then merged."""

    def runs(self, with_retry=True):
        root = Path(tempfile.mkdtemp(dir=self.tmp, prefix="estock-db-read"))
        t = base_tables()
        vendor = t.pop("Vendor")
        write_export(root / "20261118-100000", t)
        (root / "20261118-100000" / "summary.txt").write_text(
            "E-Stock database read (read only) 20261118-100000\nRESULT: PARTIAL - 25 tables read; 2 not read:\n"
            "   PC / stock.dbo.Vendor\n   PC / stock.dbo.Products (stats)\nfull tables: ...\n", encoding="utf-8-sig")
        if with_retry:
            write_export(root / "20261118-110000", {"Vendor": vendor})
            (root / "20261118-110000" / "summary.txt").write_text(
                "E-Stock database read (read only) 20261118-110000\nretry of run 20261118-100000: 1 table(s)\n"
                "only: stock.dbo.Vendor\nRESULT: PASS - 1 tables, 2 rows read.\n", encoding="utf-8-sig")
        return root

    def test_a_partial_read_blocks_and_status_says_so(self):
        root = self.runs(with_retry=False)
        ex = estock_db.Export(root)
        self.assertEqual(ex.missing, ["stock.dbo.vendor"])               # the (stats) one was exported
        from app.core.errors import ValidationFailed
        with self.assertRaises(ValidationFailed):                         # Vendor is required
            self.run_import(folder=root)
        code, out = self.status(root)
        self.assertEqual(code, 3)
        self.assertIn("NOT READ: stock.dbo.vendor", out)

    def test_the_retry_completes_the_read_and_the_move_passes(self):
        root = self.runs()
        ex = estock_db.Export(root)
        self.assertEqual((ex.missing, ex.runs), ([], ["20261118-100000", "20261118-110000"]))
        self.assertTrue(ex.has("Vendor") and ex.has("Products"))
        res = self.run_import(folder=root)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertEqual(self.db.one("SELECT count(*) n FROM suppliers")["n"], 2)
        code, out = self.status(root)
        self.assertEqual(code, 0)
        self.assertIn("READ: COMPLETE", out)

    def test_a_newer_read_of_a_table_replaces_the_first(self):
        root = self.runs()
        t = base_tables()
        t["Customer"][0]["customer_current_money"] = "130.0000"
        t["Gedo_customers"] = []
        write_export(root / "20261118-120000", {"Customer": t["Customer"]})
        (root / "20261118-120000" / "summary.txt").write_text(
            "retry of run 20261118-110000: 1 table(s)\nonly: Customer\nRESULT: PASS - 1 tables\n", encoding="utf-8-sig")
        ex = estock_db.Export(root)
        self.assertEqual(ex.rows("Customer")[0]["customer_current_money"], "130.0000")

    def test_no_full_read(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "x").mkdir()
        (root / "x" / "summary.txt").write_text("only: Customer\nRESULT: PASS\n", encoding="utf-8")
        with self.assertRaises(estock_db.ExportError):
            estock_db.Export(root)

    def status(self, root):
        import manage
        a = SimpleNamespace(status=True, dir=str(root), database=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                manage.cmd_import_estock_db(a, SimpleNamespace())
                return 0, buf.getvalue()
            except SystemExit as e:
                return e.code, buf.getvalue()

    def test_bat_checks_the_read_and_rereads_once_before_anything(self):
        L = TestBat()
        L.setUp()
        lines = L.lines
        status = [i for i, l in enumerate(lines) if "--status" in l and '"%MANAGE%" import-estock-db' in l]
        self.assertEqual(len(status), 2)
        reread = next(i for i, l in enumerate(lines) if l == 'if "%RC%"=="3" call :reread')
        stop = next(i for i, l in enumerate(lines) if l == "call :stopprog")
        self.assertTrue(status[0] < reread < status[1] < stop)
        self.assertTrue(lines[status[1] + 1].startswith("if errorlevel 1 (set"))
        sub = lines[lines.index(":reread"):]
        self.assertIn('-File "%~dp0estock-db-read.ps1" -RetryFailed <nul', sub[2])
        self.assertTrue(all('--dir "%READS%"' in l for l in lines if '"%MANAGE%" import-estock-db' in l))


class TestManagerSchemaReview(Base):
    """Manager 22:15, from E-Stock's real summary.txt."""

    def test_a_row_read_twice_blocks(self):
        t = base_tables()
        t["Product_Amount"].append(dict(t["Product_Amount"][0]))           # pa_id 1 twice
        res = self.run_import(tables=t)
        self.assertIn(("Product_Amount", "pa_id"), {(b["table"], b["field"]) for b in res["plan"].blocking})
        self.assertFalse(self.db.one("SELECT 1 FROM items"))

    def test_tables_that_end_at_different_times_are_listed(self):
        t = base_tables()
        t["Gedo_Financial"] = [{"gf_id": "5", "insert_date": "2026-11-18T10:13:00.000"}]
        t["Sales_header"][0]["gf_id"] = "7"
        res = self.run_import(tables=t)
        got = {(d["field"], d["severity"]) for d in res["plan"].dq}
        self.assertIn(("insert_date", "review"), got)
        self.assertIn(("gf_id", "review"), got)

    def test_branch_mirror_rows_of_the_other_pharmacy_are_left_out(self):
        t = base_tables()
        t["co_inf"] = [{"branch_id": "3", "co_name": "x"}]
        t["Branches_sales_details"].append({"branch_id": "2", "details_id": "1", "sales_id": "1", "product_id": "3",
                                            "amount": "9", "sale_unit_change": "5", "insert_date": SOLD})
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertNotIn(("102", "sale_unit_change"), {(d["code"], d["field"]) for d in res["plan"].dq})
        from app.dataimport import estock_db as e
        self.assertEqual(len(e.sales_lines(e.Export(self.export(t)))), 2)

    def test_product_amount_is_compared_with_the_branch_mirror(self):
        t = base_tables()
        t["co_inf"] = [{"branch_id": "1"}]
        t["Branches_Product_Amount"] = [dict(r, branch_id="1") for r in t["Product_Amount"]]
        res = self.run_import(tables=t)
        self.assertTrue(res["plan"].stock_source["mirror_check"]["equal"])
        self.assertEqual(res["plan"].stock_source["decision"]["key"], "Product_Amount:3")
        t["Branches_Product_Amount"][0]["amount"] = "11"
        self.db.close()
        Base.setUp(self)
        res = self.run_import(tables=t)
        self.assertFalse(res["plan"].stock_source["mirror_check"]["equal"])
        self.assertIn(("stock", "mirror"), {(d["table"], d["field"]) for d in res["plan"].dq})

    def test_the_hospital_store_is_listed_in_full_and_never_in_the_pharmacy_stock(self):
        res = self.run_import()
        row = [d for d in res["plan"].dq if d["field"] == "store_id"][0]
        self.assertEqual((row["severity"], row["estock_id"], row["value_egp"]), ("review", "2", "560.00"))
        self.assertIn("ملك المستشفى", row["action"])
        text = Path(res["reports"]["hospital_stock"]).read_text(encoding="utf-8-sig")
        self.assertIn("Testaclav 1g Tab", text)
        self.assertIn("2027-01-01", text)
        self.assertEqual(self.units("102"), 28)                  # the pharmacy's own 28; the hospital's 4 packs apart

    def test_stock_rows_keep_vendor_and_tax_in_the_import_row(self):
        import json
        self.run_import()
        raw = [json.loads(r["raw"]) for r in self.db.all(
            "SELECT r.raw FROM import_rows r JOIN import_runs u ON u.id=r.run_id WHERE u.kind='opening_stock'")]
        self.assertTrue(all("vendor_id" in x and "tax_price" in x and "counter_id" in x for x in raw))


class TestItemExtras(Base):
    def test_unit_names_and_everything_else_is_kept_and_listed(self):
        import json
        t = base_tables()
        t["Products"][0].update({"product_unit1": "1", "notes": "ممنوع للحوامل", "product_fast_code": "TM5",
                                 "group_id": "4", "product_max_disc": "10", "unit2_sell_price": "10.0000"})
        t["Product_groups"] = [{"group_id": "4", "group_name_ar": "مسكنات"}]
        t["Products"].append(product(10, "108", "Testnoexp Gel", product_has_expire="0"))
        t["Product_Amount"].append(stock_row(50, 10, 1, "2", "5.0000", ""))
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        it = self.item("100")
        self.assertEqual((it["pack_unit_name"], it["base_unit_name"]), ("علبة", "شريط"))
        raw = json.loads(self.db.one("SELECT r.raw FROM import_rows r WHERE r.target_id=?", (it["id"],))["raw"])
        self.assertEqual((raw["extras"]["notes"], raw["extras"]["group"], raw["extras"]["fast_code"]),
                         ("ممنوع للحوامل", "مسكنات", "TM5"))
        csv_text = Path(res["reports"]["item_extras"]).read_text(encoding="utf-8-sig")
        self.assertIn("ممنوع للحوامل", csv_text)
        self.assertIn("max_disc", csv_text)
        exp = {d["code"]: d["severity"] for d in res["plan"].dq if d["field"] == "expiry"}
        self.assertEqual(exp["108"], "info")                         # E-Stock says: no expiry for this item


class TestPartyExtras(Base):
    def test_customer_extras_in_notes_and_sell_price_buy_reported(self):
        t = base_tables()
        t["Customer"][0].update({"customer_pay_type": "2", "sales_man": "7", "sell_price_buy": "1"})
        t["Vendor"][0]["vendor_max_money"] = "50000.0000"
        res = self.run_import(tables=t)
        notes = self.db.one("SELECT notes FROM customers WHERE name='أحمد تجربة'")["notes"]
        for word in ("طريقة الدفع في إي ستوك: 2", "المندوب: 7", "البيع بسعر الشراء: 1"):
            self.assertIn(word, notes)
        self.assertIn(("C1", "sell_price_buy", "review"), {(d["code"], d["field"], d["severity"]) for d in res["plan"].dq})
        self.assertIn("vendor_max_money: 50000.0000", Path(res["reports"]["supplier_contacts"]).read_text(encoding="utf-8-sig"))


class TestArchiveSecretsAndTickets(Base):
    def test_plain_text_passwords_are_not_copied_and_tickets_are_counted_trimmed(self):
        t = base_tables()
        t["Employee"] = [{"emp_id": "1", "emp_name": "موظف تجربة", "pass": "secret-1"}]
        t["Sales_header"][0].update({"ticket_name": "   ", "ticket_id": "", "ticket_num": "  ", "bill_number": "  "})
        t["Sales_header"][1]["ticket_num"] = "T-123      "
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        con = estock_archive.open_ro(self.tmp / estock_archive.NAME)
        try:
            cols = [r[1] for r in con.execute('PRAGMA table_info("e_Employee")')]
            self.assertNotIn("pass", cols)
            self.assertEqual(con.execute("SELECT name FROM employees WHERE estock_id='1'").fetchone()[0], "موظف تجربة")
            self.assertIn("Employee.pass", con.execute("SELECT value FROM meta WHERE key='left_out_columns'").fetchone()[0])
            self.assertTrue(con.execute("SELECT 1 FROM sqlite_master WHERE name='ix_e_Sales_header_ticket_num'").fetchone())
        finally:
            con.close()
        self.assertIn(("Employee.pass", "info"), {(d["field"], d["severity"]) for d in res["plan"].dq})   # owner: not needed
        self.assertEqual(res["plan"].checks["tickets"]["with_ticket_after_trim"], 1)
        from app.dataimport import estock_history as h
        self.assertEqual([r["ticket_no"] for r in h.invoices(self.db, self.ctx, "sale") if r["estock_id"] == "2"], ["T-123"])


class TestOwnerReportLines(Base):
    def test_one_line_each_for_the_owner(self):
        t = base_tables()
        t["Sales_header"].append({"sales_id": "3", "customer_id": "77", "total_bill_net": "5", "insert_date": SOLD})
        t["Purchase_header"].append({"purchase_id": "2", "vendor_id": "9", "total_bill": "404.1500",
                                     "bill_date": "2026-07-11T00:00:00", "bill_number": "PK"})
        t["Gedo_branches"] = [{"gb_id": "1", "total": "100.0000", "insert_date": "2026-05-29T10:00:00"},
                              {"gb_id": "2", "total": "250.5000", "insert_date": "2026-05-30T10:00:00"}]
        t["Branches_sales_details"] += [
            {"branch_id": "3", "details_id": "8", "sales_id": "1", "product_id": "1", "total_sell": "55232.6600", "insert_date": SOLD},
            {"branch_id": "3", "details_id": "9", "sales_id": "2", "product_id": "1", "total_sell": "-55232.6600", "insert_date": SOLD}]
        t["Purchase_header"].append({"purchase_id": "4", "vendor_id": "1", "total_bill": "404.1500",
                                     "bill_date": "2026-07-11T00:00:00", "bill_number": "PK-1"})
        t["Employee"] = [{"emp_id": "5", "emp_name": "موظف قديم", "pass": "x"}]
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        rows = {(d["table"], d["field"]): d for d in res["plan"].dq if d["action"].startswith("OWNER")}
        every = {(d["table"], d["field"]): d for d in res["plan"].dq}
        # manager 20/11 00:25: history-only lines (unknown ids in old invoices, a large sale line) are for review,
        # not the owner's decisions; they change no money and no stock
        self.assertEqual(every[("Customer", "customer_id")]["estock_id"], "77")
        self.assertEqual(every[("Vendor", "vendor_id")]["estock_id"], "9")
        self.assertNotIn(("Customer", "customer_id"), rows)
        self.assertEqual(rows[("Purchase_header", "purchase")]["value_egp"], "404.15")   # vendor 9 unknown: a line
        self.assertIn("سطر عكسه", every[("sales lines", "line value")]["action"])
        self.assertNotIn(("sales lines", "line value"), rows)
        # owner 19/11: a parked purchase becomes a draft purchase (no stock, no balance)
        d = self.db.one("SELECT * FROM purchase_invoices WHERE invoice_no='PK-1'")
        self.assertEqual((d["status"], d["declared_total"], d["invoice_date"]), ("draft", 40415, "2026-07-11"))
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_ledger WHERE kind='purchase'"))
        # the other pharmacy: its own account with the last balance
        b = self.db.one("SELECT id FROM suppliers WHERE name=?", (estock_db.BRANCH_ACCOUNT_NAME,))
        self.assertEqual(self.db.one("SELECT sum(amount) a FROM supplier_ledger WHERE supplier_id=?", (b["id"],))["a"], 25050)
        # staff: stopped users with their names
        u = self.db.one("SELECT u.id, u.full_name, m.is_active FROM users u JOIN memberships m ON m.user_id=u.id"
                        " WHERE u.username='estock-5'")
        self.assertEqual((u["full_name"], u["is_active"]), ("موظف قديم", 0))
        # the same export again changes nothing
        n = (self.db.one("SELECT count(*) n FROM purchase_invoices")["n"], self.db.one("SELECT count(*) n FROM users")["n"])
        res = self.run_import(folder=self._last)
        self.assertEqual((self.db.one("SELECT count(*) n FROM purchase_invoices")["n"],
                          self.db.one("SELECT count(*) n FROM users")["n"]), n)


class TestCashDepots(Base):
    def test_every_depot_is_in_the_plan_and_the_report_not_posted(self):
        t = base_tables()
        t["Cash_depots"] = [{"cd_id": "1", "cd_name": "الخزنة الرئيسية", "cd_class": "1", "cd_balance": "12500.2500"},
                            {"cd_id": "2", "cd_name": "إنستاباي", "cd_class": "3", "cd_balance": "830.0000"},
                            {"cd_id": "3", "cd_name": "فودافون كاش", "cd_class": "3", "cd_balance": "-15.0000"}]
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        d = res["plan"].checks["money_info"]["cash_depots"]
        self.assertEqual([(x["estock_id"], x["name"], x["class"], x["balance"]) for x in d],
                         [("1", "الخزنة الرئيسية", "1", "12500.25"), ("2", "إنستاباي", "3", "830.00"),
                          ("3", "فودافون كاش", "3", "-15.00")])
        # owner 20/11 00:50: the treasury's E-Stock figure is a reference (info), the two wallets are reviewed
        self.assertEqual(sum(1 for x in res["plan"].dq if x["table"] == "Cash_depots" and x["severity"] == "review"), 2)
        self.assertTrue(d[0]["reference_only"])
        self.assertIn("إنستاباي", Path(res["reports"]["reconciliation"]).read_text(encoding="utf-8-sig"))
        self.assertFalse(self.db.one("SELECT 1 FROM cash_movements"))


class TestCreditLimits(Base):
    """Manager 22:35: a limit above 1,000,000 means "no limit" in E-Stock; 0 in the program means no credit."""

    def sell_on_credit(self, cust_name, amount_units=1):
        from app.sales.cash import CashDrawer
        from app.sales.service import Sales
        s = Sales(self.db)
        cid = self.db.one("SELECT id FROM customers WHERE name=?", (cust_name,))["id"]
        if not self.db.one("SELECT 1 FROM cash_shifts WHERE status='open'"):
            CashDrawer(self.db).open(self.ctx, 0)
        sid = s.create(self.ctx, cid)
        s.add_line(self.ctx, sid, self.item("100")["id"], units=amount_units)
        tot = s.get(self.ctx, sid)["total"]
        f = s.safety_check(self.ctx, sid)
        return s.complete(self.ctx, sid, on_credit=tot, safety_decisions={i: "ok" for i in range(len(f))})

    def test_no_limit_in_estock_keeps_credit_sales_working(self):
        res = self.run_import()
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.sell_on_credit("أحمد تجربة")                                # would be refused with a 0 limit
        row = [d for d in res["plan"].dq if d["code"] == "C1" and d["field"] == "customer_max_money"][0]
        self.assertIn("من غير حد", row["action"])

    def test_zero_limit_means_no_credit_in_the_program(self):
        from app.core.errors import Conflict as C
        t = base_tables()
        t["Customer"][1]["customer_max_money"] = "0"
        res = self.run_import(tables=t)
        with self.assertRaises(C) as e:                          # 30 EGP on top of -15.50 is over a 0 limit
            self.sell_on_credit("منى تجربة", amount_units=3)
        self.assertEqual(e.exception.code, "credit_limit")
        # owner 19/11: 0 in E-Stock while buying on credit = no limit, as E-Stock behaved
        t["Customer"][0]["customer_max_money"] = "0"                    # Ahmed: 0 in E-Stock and owes 120
        self.db.close()
        Base.setUp(self)
        res = self.run_import(tables=t)
        self.assertIn(("C1", "customer_max_money", "info"),
                      {(d["code"], d["field"], d["severity"]) for d in res["plan"].dq})
        self.assertEqual(self.db.one("SELECT credit_limit FROM customers WHERE name='أحمد تجربة'")["credit_limit"],
                         estock_db.NO_LIMIT)
        self.sell_on_credit("أحمد تجربة")


class TestCashDepotsAsOnTheScreen(Base):
    """Manager 22:28, from E-Stock's «النقدية المتاحة»: all 11 move as they are; 6 and 10 listed for the owner."""

    def test_eleven_depots(self):
        names = ["نقطة بيع 1", "خزينة 1", "حساب بنك مصر", "نقطة بيع 2", "نقطة بيع 3", "0000", "انستا باي",
                 "فودافون كاش", "اتصالات كاش", "فيزا باى موب - موقوف", "امان"]
        t = base_tables()
        t["Cash_depots"] = [{"cd_id": str(i), "cd_name": n, "cd_class": "1", "cd_balance": f"{i * 10}.0000"}
                            for i, n in enumerate(names, start=1)]
        t["Cash_disk_close"][0].update({"cdc_cash_id": "1", "insert_date": "2026-11-15T14:19:00.000"})
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        d = {x["estock_id"]: x for x in res["plan"].checks["money_info"]["cash_depots"]}
        self.assertEqual(len(d), 11)
        self.assertEqual([d[i]["role"] for i in ("1", "2", "3", "6", "7", "8", "9", "10", "11")],
                         ["drawer", "treasury", "bank", "junk", "wallet", "wallet", "wallet", "wallet", "wallet"])
        self.assertEqual(d["1"]["last_close"], "2026-11-15T14:19:00.000")
        # owner 19/11: 1 = the shift drawer (first shift opening), 6 = no account, 10 = a stopped account
        self.assertEqual({k: d[k]["action"] for k in d},
                         {"1": "first_shift_opening", "2": "account", "3": "account", "4": "account", "5": "account",
                          "6": "no_account", "7": "account", "8": "account", "9": "account", "10": "account_stopped",
                          "11": "account"})
        owner = {x["estock_id"] for x in res["plan"].dq if x["table"] == "Cash_depots" and x["action"].startswith("OWNER")}
        self.assertEqual(owner, {"6"})                          # «0000» holds 60.00 here: one line with the figure
        self.assertIn("60.00", [x for x in res["plan"].dq if x["table"] == "Cash_depots" and x["estock_id"] == "6"][0]["action"])
        self.assertTrue(any(x["field"] == "last_close" for x in res["plan"].dq))


class TestScreenTour2245(Base):
    """Manager 22:45: internal accounts, purchase classes, junk masters, E-Stock settings."""

    def test_misc_sources_supplier_and_internal_consumption_customer(self):
        """Owner 19/11: «جرد» is a real supplier (occasional sources); «الصيدلية» is internal consumption."""
        t = base_tables()
        t["Vendor"].append({"vendor_id": "3", "vendor_code": "V3", "vendor_name_ar": "جرد", "vendor_current_money": "900.0000"})
        t["Customer"].append({"customer_id": "4", "customer_code": "C4", "customer_name_ar": "الصيدليه",
                              "customer_current_money": "300.0000", "sell_price_buy": "1"})
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        v = self.db.one("SELECT id FROM suppliers WHERE name='مصادر متفرقة (جرد)'")
        self.assertTrue(v)
        self.assertEqual(self.db.one("SELECT sum(amount) a FROM supplier_ledger WHERE supplier_id=?", (v["id"],))["a"], 90000)
        con = estock_archive.open_ro(self.tmp / estock_archive.NAME)
        try:                                           # the E-Stock id still maps to it; the old name stays there
            self.assertEqual(con.execute("SELECT program_id FROM map_suppliers WHERE estock_id='3'").fetchone()[0], v["id"])
            self.assertEqual(con.execute('SELECT vendor_name_ar FROM "e_Vendor" WHERE vendor_id=\'3\'').fetchone()[0], "جرد")
        finally:
            con.close()
        c = self.db.one("SELECT name, notes FROM customers WHERE name LIKE '%حساب داخلي%'")
        self.assertIn("استهلاك الصيدلية الداخلي", c["notes"])
        s, cu = res["reconciliation"]["suppliers"], res["reconciliation"]["customers"]
        self.assertEqual((s["internal_accounts"], s["real_balance_total"]), (0, "1380.00"))
        self.assertEqual((cu["internal_balance_total"], cu["real_balance_total"]), ("300.00", "104.50"))
        self.assertNotIn("internal", {d["field"] for d in res["plan"].dq if d["table"] == "Vendor"})

    def test_purchase_classes_are_counted_for_the_owner(self):
        t = base_tables()
        t["Purchase_header"][0]["class"] = "1"
        t["Purchase_header"] += [{"purchase_id": "2", "vendor_id": "1", "class": "3", "total_bill": "700.0000"},
                                 {"purchase_id": "3", "vendor_id": "2", "class": "3", "total_bill": "300.0000",
                                  "total_after_back": "250.0000"}]
        res = self.run_import(tables=t)
        pc = res["plan"].checks["purchase_classes"]
        self.assertEqual(pc["3"], {"invoices": 2, "total_bill": "1000.00", "after_returns": "950.00", "vendors": 2,
                                   "meaning": "consignment"})                     # owner 20/11 00:50
        self.assertIn("تحت التصريف", [d for d in res["plan"].dq if d["field"] == "class" and d["estock_value"] == "3"][0]["action"])

    def test_junk_master_names_are_listed_and_not_used(self):
        t = base_tables()
        t["Product_units"].append({"unit_id": "6", "unit_name_ar": "6"})
        t["Products"][1].update({"product_unit1": "6"})
        res = self.run_import(tables=t)
        self.assertIn(("Product_units", "6"), {(d["table"], d["estock_id"]) for d in res["plan"].dq if d["field"] == "name"})
        self.assertEqual(self.item("101")["pack_unit_name"], "pack")             # not "6"

    def test_estock_settings_are_in_the_report(self):
        res = self.run_import()
        rec = Path(res["reports"]["reconciliation"]).read_text(encoding="utf-8-sig")
        self.assertIn("مرتجع البيع خلال 7 أيام", rec)
        self.assertIn("مفيش إيصال تحت 20 جنيه", rec)


class TestCodexReview345b(Base):
    """Codex on #345 @5eb8cd6 (each failed before the fix)."""

    def test_p1_the_branch_account_honours_its_confirmed_balance(self):
        from openpyxl import load_workbook
        t = base_tables()
        t["Gedo_branches"] = [{"gb_id": "1", "total": "100.0000", "insert_date": "2026-05-30T10:00:00"}]
        first = self.run_import(tables=t, write_archive=False)
        wb = load_workbook(first["reports"]["openings"])
        for r in wb.active.iter_rows(min_row=2):
            if r[1].value == estock_db.BRANCH_ACCOUNT_ID:
                r[5].value = -100
        conf = self.tmp / "c.xlsx"
        wb.save(conf)
        self.db.close()
        Base.setUp(self)
        res = self.run_import(tables=t, confirm_file=conf)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        b = self.db.one("SELECT id FROM suppliers WHERE name=?", (estock_db.BRANCH_ACCOUNT_NAME,))["id"]
        self.assertEqual(self.db.one("SELECT sum(amount) a FROM supplier_ledger WHERE supplier_id=?", (b,))["a"], -10000)

    def test_p2_a_retry_that_exported_nothing_does_not_break_the_read(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        t = base_tables()
        vendor = t.pop("Vendor")
        write_export(root / "20261118-100000", t)
        (root / "20261118-100000" / "summary.txt").write_text(
            "RESULT: PARTIAL - x; 1 not read:\n   PC / stock.dbo.Vendor\n", encoding="utf-8-sig")
        (root / "20261118-110000" / "tables").mkdir(parents=True)          # a retry that failed: no manifest
        (root / "20261118-110000" / "summary.txt").write_text(
            "retry of run 20261118-100000: 1 table(s)\nRESULT: PARTIAL - 0 tables; 1 not read:\n   PC / stock.dbo.Vendor\n",
            encoding="utf-8-sig")
        write_export(root / "20261118-120000", {"Vendor": vendor})
        (root / "20261118-120000" / "summary.txt").write_text(
            "retry of run 20261118-110000: 1 table(s)\nRESULT: PASS - 1 tables\n", encoding="utf-8-sig")
        ex = estock_db.Export(root)
        self.assertEqual((ex.missing, ex.runs), ([], ["20261118-100000", "20261118-120000"]))


class TestCodexReview345c(Base):
    """Codex on #345 @5dcf3e5 (each failed before the fix)."""

    def test_p2_a_failed_read_is_never_complete(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        write_export(root / "20261118-100000", base_tables())
        (root / "20261118-100000" / "summary.txt").write_text(
            "RESULT: FAIL - the connection dropped (nothing in E-Stock was changed)\n", encoding="utf-8-sig")
        with self.assertRaises(estock_db.ExportError):
            estock_db.Export(root)
        one = root / "20261118-100000"                      # the same run given directly
        self.assertTrue(estock_db.Export(one).missing)

    def test_p2_a_blank_expiry_does_not_break_the_mirror_check(self):
        t = base_tables()
        t["co_inf"] = [{"branch_id": "1"}]
        t["Product_Amount"].append(stock_row(60, 1, 1, "2", "24.0000", ""))       # no expiry, same item and store
        t["Branches_Product_Amount"] = [dict(r, branch_id="1") for r in t["Product_Amount"]]
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertTrue(res["plan"].stock_source["mirror_check"]["equal"])


class TestCodexReview345d(Base):
    """Codex on #345 @04d6b9e (each failed before the fix)."""

    def test_p2_fully_returned_purchase_counts_zero_after_returns(self):
        t = base_tables()
        t["Purchase_header"] += [{"purchase_id": "2", "vendor_id": "1", "class": "7", "total_bill": "500.0000",
                                  "total_after_back": "0.0000"}]
        res = self.run_import(tables=t)
        self.assertEqual(res["plan"].checks["purchase_classes"]["7"]["after_returns"], "0.00")

    def test_p2_unit_names_follow_the_decided_pack(self):
        t = base_tables()
        t["Products"].append(product(5, "104", "Testbroken Tab", product_unit2="2", product_unit1_2=""))
        folder = self.export(t)
        packs = self.tmp / "packs.json"
        packs.write_text('{"104": 3}', encoding="utf-8")
        res = self.run_import(folder=folder, packs_file=packs)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        it = self.item("104")
        self.assertEqual((it["pack_size"], it["pack_unit_name"], it["base_unit_name"]), (3, "علبة", "شريط"))


class TestRehearsal(Base):
    """Codex on #362: the daytime trial runs while the program still holds trial data; the real database
    refuses the move then (exit 4), so the trial runs on a NEW EMPTY database and writes nothing real."""
    cmd = TestCommand.cmd

    def _trial_sale(self):
        from app.catalog import service as cat
        from app.inventory import service as inv
        iid = cat.create_item(self.db, self.ctx, {"name_en": "Trial Tab", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=iid, qty_delta=5, movement_type="adjustment",
                              unit_cost=Decimal("5"), ref_type="trial", ref_id="t1", note="trial")

    def test_a_database_with_trial_data_refuses_with_exit_4(self):
        self._trial_sale()
        code, out, _ = self.cmd(self.export(), False)
        self.assertEqual(code, 4, out)

    def test_rehearsal_on_an_empty_database_touches_nothing_real(self):
        self._trial_sale()
        before = self.db.one("SELECT count(*) n FROM items")["n"]
        code, out, s = self.cmd(self.export(), False, rehearsal=True)
        self.assertEqual(code, 0, out)
        self.assertIn("RECONCILIATION: OK", out)
        self.assertIn("REHEARSAL", out)
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], before)       # the real one is untouched
        self.assertFalse((Path(self.db.path).parent / estock_archive.NAME).exists())
        self.assertTrue((self.tmp / "oFalse" / "openings-to-confirm.xlsx").exists())   # what the trial is for

    def test_rehearsal_never_applies(self):
        code, out, _ = self.cmd(self.export(), True, rehearsal=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(self.db.one("SELECT 1 FROM items"))
