"""Every ledger the owner reconciles is append-only in the database itself: an UPDATE or DELETE of any row is
refused by a trigger, whatever program writes to the file. One sweep over all of them, so a later migration
that drops or forgets a trigger fails here. Synthetic data only."""
import sqlite3

from app.catalog import service as cat
from tests.test_owner_plan_priority_1_2 import PlanCase

LEDGERS = ("audit_log", "stock_movements", "customer_ledger", "supplier_ledger", "cash_movements", "safety_events")


class AppendOnlySweep(PlanCase):
    def setUp(self):
        super().setUp()
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        cust = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 100000})["id"]
        self.sell(self.pan, payments=[{"method": "credit", "amount": 4000}], customer=cust, units=2)   # ledger + safety
        self.call("POST", "/api/cash/movements", json={"amount": 500, "kind": "payout", "note": "تجربة"})
        sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.call("POST", f"/api/suppliers/{sup}/pay", json={"amount": 1000, "method": "cash"})

    def test_no_row_of_any_ledger_can_be_changed_or_deleted(self):
        conn = sqlite3.connect(self.db.path)                         # straight to the file, no program rules
        try:
            for table in LEDGERS:
                with self.subTest(table=table):
                    n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    self.assertGreater(n, 0, f"{table} has no row to try")
                    col = "rowid"
                    with self.assertRaises(sqlite3.DatabaseError):
                        conn.execute(f"UPDATE {table} SET {col}={col} WHERE rowid=(SELECT min(rowid) FROM {table})")
                    with self.assertRaises(sqlite3.DatabaseError):
                        conn.execute(f"DELETE FROM {table} WHERE rowid=(SELECT min(rowid) FROM {table})")
                    conn.rollback()
                    self.assertEqual(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], n)
        finally:
            conn.close()

    def test_every_ledger_has_both_triggers(self):
        conn = sqlite3.connect(self.db.path)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        finally:
            conn.close()
        for table in LEDGERS:
            self.assertIn(f"{table}_no_update", names)
            self.assertIn(f"{table}_no_delete", names)
