"""Update 1 (owner, launch day): every purchase invoice has the program's own serial number (per branch), shown
next to the supplier's invoice number («رقم فاتورة المورد»), like E-Stock. Migration 0041 numbers the invoices
already there in the order they were created; new ones follow on. The help button shows on an invoice too."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from app import config
from app.catalog import service as cat
from app.core import accounts
from app.db import MIGRATIONS_DIR, Database
from app.purchasing.service import Purchasing
from tests.helpers import OWNER_PW, DBTestCase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


class NewInvoices(DBTestCase):
    def test_serials_follow_on(self):
        p = Purchasing(self.db, config.load())
        sid = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        ids = [p.create_manual(self.ctx, supplier_id=sid) for _ in range(3)]
        self.assertEqual([p.get(self.ctx, i)["invoice"]["serial_no"] for i in ids], [1, 2, 3])
        self.assertEqual([r["serial_no"] for r in sorted(p.list(self.ctx, None), key=lambda r: r["serial_no"])],
                         [1, 2, 3])


class Backfill(unittest.TestCase):
    def test_existing_invoices_numbered_in_creation_order(self):
        tmp = Path(tempfile.mkdtemp(prefix="pharm-test-"))
        try:
            old = tmp / "old"
            old.mkdir()
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.name < "0041":
                    shutil.copy(f, old / f.name)
            db = Database(tmp / "t.db")
            db.migrate(old)
            accounts.create_organization(db, name="ص", owner_username="owner", owner_full_name="O",
                                         owner_password=OWNER_PW)
            org = db.one("SELECT id FROM organizations")["id"]
            br = db.one("SELECT id FROM branches")["id"]
            with db.transaction():
                for i, at in (("b", "2026-11-21T10:00:00.000Z"), ("a", "2026-11-21T09:00:00.000Z"),
                              ("c", "2026-11-21T11:00:00.000Z")):
                    db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, source_type, created_at)"
                               " VALUES (?,?,?,'manual',?)", (i, org, br, at))
            db.migrate()
            self.assertEqual([tuple(r) for r in db.all("SELECT id, serial_no FROM purchase_invoices ORDER BY id")],
                             [("a", 1), ("b", 2), ("c", 3)])
            self.assertEqual(db.one("SELECT next_no FROM branch_counters WHERE kind='purchase'")["next_no"], 4)
            db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Screen(unittest.TestCase):
    def test_labels_and_help_button(self):
        self.assertIn('<label class="f">رقم فاتورة المورد <input data-h="invoice_no"', JS)
        self.assertIn("مسلسل ${esc(inv.serial_no)}", JS)
        self.assertRegex(JS, r'if \(/\^#\\/purchases\\/\[\\w-\]\+\$/\.test\(h\)\) return "purchase";')
        self.assertIn("  purchase: [", JS)


if __name__ == "__main__":
    unittest.main()
