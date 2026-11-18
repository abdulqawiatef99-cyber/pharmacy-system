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
    t["Gedo_customers"] = [{"gc_id": "1", "customer_id": "1", "gc_for_him": "30.0000", "gc_for_me": "150.0000"}]
    t["Gedo_Vendors"] = [{"gv_id": "1", "vendor_id": "1", "gv_for_him": "500.0000", "gv_for_me": "0.0000"}]
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
        return estock_db.run(self.db, self.ctx, export_dir=folder, out_dir=self.tmp / "reports",
                             archive_path=self.tmp / estock_archive.NAME, write_archive=write_archive, **kw)

    def dq(self, res):
        return res["plan"].dq

    def item(self, code):
        return self.db.one("SELECT * FROM items WHERE code=?", (code,))

    def units(self, code):
        return self.db.one("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE item_id=?",
                           (self.item(code)["id"],))["q"]


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
        self.assertIsNone(self.db.one("SELECT batch_id FROM stock_movements WHERE item_id=?", (self.item("E4")["id"],))["batch_id"])
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
        self.assertEqual((c["أحمد تجربة"]["credit_limit"], c["منى تجربة"]["credit_limit"]), (0, 50000))
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
        self.assertEqual(h["Sales_header"], {"manifest": 2, "csv": 2, "archive": 2})
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
        t = base_tables()
        t["Branches_Product_Amount"] = [stock_row(9, 1, 1, "50", "24.0000", "2027-05-01T00:00:00", branch="1"),
                                        stock_row(10, 3, 1, "1", "140.0000", "2027-05-01T00:00:00", branch="1")]
        folder = self.export(t)
        res = self.run_import(folder=folder)
        self.assertFalse(res["ok"])
        self.assertFalse(res["applied"])
        self.assertFalse(self.db.one("SELECT 1 FROM items"))
        self.assertIn("stock_source", {b["field"] for b in res["plan"].blocking})
        res = self.run_import(folder=folder, stock_source="Product_Amount:3")
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
        t["Gedo_customers"] = [{"gc_id": "1", "customer_id": "1", "gc_for_him": "150.0000", "gc_for_me": "30.0000"}]
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
        self.assertTrue((s.data_dir / estock_archive.NAME).exists())
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
        runs = [i for i, l in enumerate(L) if '"%MANAGE%" import-estock-db' in l and "--status" not in l]
        self.assertEqual(len(runs), 2)
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
        res = self.run_import(tables=t)
        self.assertFalse(res["ok"])
        self.assertIn(("C1", "customer_current_money"), {(b["code"], b["field"]) for b in res["plan"].blocking})
        self.assertFalse(self.db.one("SELECT 1 FROM customer_ledger"))

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

    def test_no_import_step_runs_after_a_confirmation_in_the_program(self):
        self.ob.confirm(self.db, self.ctx, "supplier", self.v1, 45000)
        with self.assertRaises(Conflict):
            self.run_import(folder=self.folder)


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

    def test_treasury_and_wallets_are_information_only(self):
        t = base_tables()
        t["Gedo_Financial"] = [{"gf_id": "1", "gf_for_me": "1500.5000", "gf_for_him": "200.0000", "total": "1300.5000"}]
        t["Sales_header"][0]["network_id"] = "2"
        t["Sales_header"][0]["network_money"] = "54.0000"
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        mi = res["plan"].checks["money_info"]
        tables = {x["table"]: x for x in mi["tables"]}
        self.assertEqual(tables["Gedo_Financial"]["sums"]["gf_for_me"], "1500.50")
        self.assertIn("Cash_disk_close", tables)
        self.assertEqual(mi["last_shift_close"][0]["counted_cash"], "999.50")
        self.assertEqual(mi["sales_by_network"], [{"source": "Sales_header.network_money", "network_id": "2", "total": "54.00"}])
        for table in ("cash_movements", "cash_shifts"):
            self.assertFalse(self.db.one(f"SELECT 1 FROM {table}"), table)
        rec = Path(res["reports"]["reconciliation"]).read_text(encoding="utf-8-sig")
        self.assertIn("مفيش رصيد خزنة ولا محفظة", rec)
        self.assertIn("Gedo_Financial", rec)


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
