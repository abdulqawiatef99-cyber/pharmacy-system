"""Corrupt-looking rows in a read taken while E-Stock runs (cashier PC 19/11 15:34, Sales_details read with
NOLOCK: sales_id -7.4e18, product_id -1.57e19, amount -1.7e212, sell_price 7.66e14, disc_money -3.0e14,
exp_date 8009-02-07, update_date 0247-05-12). They are never imported as money: they go to their own table in
the archive, out of every history read, return and report, with a clear owner line. Synthetic values only."""
import sqlite3
from contextlib import closing
from pathlib import Path

from app.dataimport import estock_archive, estock_db, estock_history
from tests.test_estock_db_import import Base, base_tables

GOOD = {"details_id": "1", "sales_id": "1", "product_id": "1", "amount": "1", "sale_unit_change": "3",
        "sell_price": "30.0000", "total_sell": "30.0000", "disc_money": "0", "exp_date": "2027-05-01T00:00:00",
        "insert_date": "2026-11-18T11:00:00.000", "update_date": "2026-11-18T11:00:00.000"}
BAD = [dict(GOOD, details_id="2", sales_id="-7.4e18"),
       dict(GOOD, details_id="3", product_id="-1.57e19"),
       dict(GOOD, details_id="4", amount="-1.7e212"),
       dict(GOOD, details_id="5", sell_price="7.66e14", total_sell=""),
       dict(GOOD, details_id="6", disc_money="-3.0e14"),
       dict(GOOD, details_id="7", exp_date="8009-02-07T00:00:00"),
       dict(GOOD, details_id="8", update_date="0247-05-12T00:00:00"),
       dict(GOOD, details_id="-9223372036854775000")]


def tables():
    t = base_tables()
    t["Sales_details"] = [GOOD] + BAD
    return t


class TestQuarantine(Base):
    def test_impossible_rows_are_found_with_their_reason(self):
        ex = estock_db.Export(self.export(tables()))
        q = ex.quarantined("Sales_details")
        self.assertEqual(len(q), len(BAD))
        self.assertNotIn(0, q)                                     # the good line stays
        reasons = " ".join(q.values())
        for col in ("sales_id", "product_id", "amount", "sell_price", "disc_money", "exp_date", "update_date",
                    "details_id"):
            self.assertIn(col, reasons)
        self.assertEqual([r["details_id"] for r in ex.rows("Sales_details")], ["1"])   # the plan never sees them

    def test_the_move_runs_and_they_never_become_money(self):
        res = self.run_import(tables=tables())
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertNotIn("7660", " ".join(d["estock_value"] for d in res["plan"].dq if d["field"] == "line value"))
        h = res["reconciliation"]["history_quarantined"]
        self.assertEqual(h["Sales_details"], len(BAD))
        with closing(sqlite3.connect(self.tmp / estock_archive.NAME)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM "e_Sales_details"').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT count(*) FROM "q_Sales_details"').fetchone()[0], len(BAD))
            self.assertTrue(con.execute('SELECT _reason FROM "q_Sales_details" LIMIT 1').fetchone()[0])
        lines = estock_history.invoice(self.db, self.ctx, "sale", "1")["lines"]
        self.assertEqual(len(lines), 1)                            # the old invoice shows the good line only

    def test_the_owner_reads_one_plain_line_per_table(self):
        res = self.run_import(tables=tables())
        owner = Path(res["reports"]["owner"]).read_text(encoding="utf-8-sig")
        sec = owner[owner.index("١. محتاج قرارك"):owner.index("٢. المطابقة")]
        self.assertIn("قيم مستحيلة", sec)
        self.assertIn("Sales_details", sec)
        self.assertIn("8 سطر", sec)
        self.assertIn("إي ستوك مقفول", sec)
        q = [d for d in res["plan"].dq if d["field"] == "impossible values"]
        self.assertEqual([d["value_egp"] for d in q], [""])          # no value: it is not money

    def test_a_clean_export_quarantines_nothing(self):
        res = self.run_import()
        self.assertEqual(res["reconciliation"]["history_quarantined"], {})


class TestCodexReview380(Base):
    """Codex on #380 @da19e69 (each failed before the fix)."""

    def test_p1_an_invoice_whose_return_history_is_quarantined_cannot_be_refunded(self):
        from app.core.errors import Conflict
        from app.sales import estock_returns as er
        from app.sales.cash import CashDrawer
        t = base_tables()
        t["Back_Sales_details"][0]["update_date"] = "0247-05-12T00:00:00"        # its prior return is unknown now
        self.assertTrue(self.run_import(tables=t)["ok"])
        CashDrawer(self.db).open(self.ctx, 0)
        with self.assertRaises(Conflict) as e:
            er.return_items(self.db, self.ctx, "1", {"1": 1}, reason="مرتجع عميل", key="k1")
        self.assertEqual(e.exception.code, "estock_history_quarantined")
        self.assertFalse(self.db.one("SELECT 1 FROM cash_movements WHERE kind='refund'"))

    def test_p1_an_unreadable_invoice_id_in_quarantine_stops_every_old_return(self):
        from app.core.errors import Conflict
        from app.sales import estock_returns as er
        t = base_tables()
        t["Back_Sales_details"].append(dict(t["Back_Sales_details"][0], back_sales_id="2", sales_id="-7.4e18"))
        self.assertTrue(self.run_import(tables=t)["ok"])
        with self.assertRaises(Conflict):
            er.return_items(self.db, self.ctx, "2", {"1": 1}, reason="مرتجع عميل", key="k2")

    def test_p2_an_archive_built_before_quarantine_is_rebuilt_not_reused(self):
        from app.dataimport import estock_archive
        folder = self.export(tables())
        ex = estock_db.Export(folder)
        path = self.tmp / "old.sqlite"
        estock_archive.build(ex, path, {}, org_id=self.ctx.org_id)
        import os
        os.chmod(path, 0o644)
        with closing(sqlite3.connect(path)) as con, con:                     # what the old version left
            con.execute("UPDATE meta SET value='1' WHERE key='format'")
            con.execute('DROP TABLE "q_Sales_details"')
            con.execute('INSERT INTO "e_Sales_details" ("details_id") VALUES (\'2\')')
        estock_archive.build(estock_db.Export(folder), path, {}, org_id=self.ctx.org_id)
        with closing(sqlite3.connect(path)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM "e_Sales_details"').fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT value FROM meta WHERE key='format'").fetchone()[0], "2")
