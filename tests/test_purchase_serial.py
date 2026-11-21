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


class BothDates(DBTestCase):
    """8b (owner 21/11 19:39): the date printed on the supplier's invoice (editable, starts as today) and the date
    the invoice was entered in the program (read-only), and when it was approved; both on the list too."""

    def test_manual_invoice_starts_today_and_the_list_carries_both(self):
        from app.sales.service import local_today
        p = Purchasing(self.db, config.load())
        sid = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        iid = p.create_manual(self.ctx, supplier_id=sid)
        inv = p.get(self.ctx, iid)["invoice"]
        self.assertEqual(inv["invoice_date"], local_today().isoformat())
        self.assertTrue(inv["created_at"])
        p.update_header(self.ctx, iid, {"invoice_date": "2026-11-15"}, inv["version"])
        row = next(r for r in p.list(self.ctx, None) if r["id"] == iid)
        self.assertEqual(row["invoice_date"], "2026-11-15")
        self.assertTrue(row["created_at"].startswith(str(local_today().year)))
        self.assertIn("approved_at", row)

    def test_screen_shows_both_in_cairo_time_searchable_and_sortable(self):
        for text in ('<label class="f">تاريخ فاتورة المورد <input data-h="invoice_date"',
                     '<label class="f">تاريخ الإدخال على السيستم <input id="pinEntered" value="${esc(cairoWhen(inv.created_at))}" readonly disabled>',
                     "تاريخ الاعتماد", 'th("created_at", "تاريخ الإدخال على السيستم")', 'th("invoice_date", "تاريخ فاتورة المورد")',
                     'id="puQ"', 'timeZone: "Africa/Cairo"'):
            self.assertIn(text, JS)


import json as _json  # noqa: E402
import shutil as _sh  # noqa: E402
import subprocess as _sp  # noqa: E402


@unittest.skipUnless(_sh.which("node"), "Node not available")
class CairoTime(unittest.TestCase):
    def test_utc_to_cairo(self):
        fn = re.search(r"\nconst cairoWhen = \(iso\) => \{.*?\n\};\n", JS, re.S).group(0)
        run = "const LOC = () => 'en-GB', H12 = () => false;" + fn + \
            "console.log(JSON.stringify([cairoWhen('2026-11-21T16:05:00.000Z'), cairoWhen(null)]))"
        r = _sp.run([_sh.which("node"), "-e", run], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_json.loads(r.stdout), ["2026-11-21 18:05", ""])     # Cairo is UTC+2 in November
