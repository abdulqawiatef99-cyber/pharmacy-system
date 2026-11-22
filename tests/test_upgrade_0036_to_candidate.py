"""The launch-candidate upgrade path (manager 19/11): a pharmacy database at schema 0036 (the cashier PC today:
develop, no 0037/0038/0039) with realistic synthetic data (a whole E-Stock move: items, batches, customers,
suppliers, balances, the archive) upgraded by the installer to the candidate's migrations, then sold on.
(Sales made by develop's own code before the upgrade are checked by the full-size manual run in
docs/INSTALLER_CANDIDATE.md: the candidate's code cannot sell on a 0036 database by design.) Balances, stock, the stock check, the audit chain and
the E-Stock archive must be the same before and after. Synthetic data only."""
import hashlib
import shutil
import tempfile
from pathlib import Path

from app.core import accounts, audit
from app.dataimport import estock_archive, estock_db, estock_synthetic
from app.db import MIGRATIONS_DIR, Database
from app.inventory import service as inventory
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase, OWNER_PW

UPTO = "0036"


class TestUpgradeFrom0036(DBTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pharm-upgrade-"))
        self.old = self.tmp / "migrations-0036"
        self.old.mkdir()
        for f in MIGRATIONS_DIR.glob("*.sql"):
            if f.name[:4] <= UPTO:
                shutil.copy2(f, self.old / f.name)
        self.db = Database(self.tmp / "pharmacy.db")
        self.db.migrate(self.old)
        accounts.create_organization(self.db, name="صيدلية تجريبية", owner_username="owner", owner_full_name="Owner",
                                     owner_password=OWNER_PW)
        self.ctx = self.login("owner", OWNER_PW)

    def _populate(self):
        folder = estock_synthetic.write(self.tmp / "estock" / "20261119-153400", scale=0.01)
        res = estock_db.run(self.db, self.ctx, export_dir=folder, out_dir=self.tmp / "reports",
                            archive_path=self.tmp / estock_archive.NAME, write_archive=True)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"][:5])

    def _snapshot(self):
        o = self.ctx.org_id
        q = lambda sql: [tuple(r) for r in self.db.all(sql, (o,))]   # noqa: E731
        arch = self.tmp / estock_archive.NAME
        return {
            "customers": q("SELECT customer_id, sum(amount) FROM customer_ledger WHERE org_id=? GROUP BY 1 ORDER BY 1"),
            "suppliers": q("SELECT supplier_id, sum(amount) FROM supplier_ledger WHERE org_id=? GROUP BY 1 ORDER BY 1"),
            "stock": q("SELECT item_id, batch_key, qty FROM stock_balances WHERE org_id=? ORDER BY 1, 2"),
            "cash": q("SELECT kind, sum(amount) FROM cash_movements WHERE org_id=? GROUP BY 1 ORDER BY 1"),
            "sales": q("SELECT id, status, total FROM sales WHERE org_id=? ORDER BY 1"),
            "counts": q("SELECT (SELECT count(*) FROM items WHERE org_id=?1), (SELECT count(*) FROM customers WHERE"
                        " org_id=?1), (SELECT count(*) FROM suppliers WHERE org_id=?1)"),
            "stock_check": inventory.verify_balances(self.db, o),
            "audit_chain": audit.verify_chain(self.db),
            "archive_sha": hashlib.sha256(arch.read_bytes()).hexdigest(),
            "archive_source": estock_archive.source_sha(arch),
        }

    def test_everything_is_the_same_after_the_upgrade(self):
        self._populate()
        before = self._snapshot()
        self.assertEqual((before["stock_check"], before["audit_chain"]), ([], []))
        applied = self.db.migrate()                                   # the installer: the candidate's migrations
        self.assertTrue(applied, "the candidate brings migrations after 0036")
        self.assertTrue(all(v[:4] > UPTO for v in applied), applied)
        self.assertEqual(self.db.unknown_migrations(), [])
        self.assertEqual(self._snapshot(), before)

    def test_after_the_upgrade_the_new_parts_work_on_the_old_data(self):
        self._populate()
        self.db.migrate()
        cols = {r["name"] for r in self.db.all("PRAGMA table_info(batches)")}
        if "owner" in cols:                                           # 0039: every old batch is the pharmacy's
            self.assertEqual(self.db.one("SELECT count(*) n FROM batches WHERE owner<>'pharmacy'")["n"], 0)
        if self.db.one("SELECT 1 FROM sqlite_master WHERE name='money_accounts'"):
            self.assertEqual(self.db.one("SELECT count(*) n FROM money_movements")["n"], 0)
        s = Sales(self.db)
        iid = self.db.one("SELECT item_id FROM stock_balances WHERE org_id=? AND qty >= 2 LIMIT 1",
                          (self.ctx.org_id,))["item_id"]
        CashDrawer(self.db).open(self.ctx, 0)
        sid = s.create(self.ctx, None)
        s.add_line(self.ctx, sid, iid, units=1)                       # a sale on the upgraded database
        # 0042 turns 0.50 cash rounding on for an existing pharmacy (the owner's choice): cash pays the rounded amount
        tot = s.get(self.ctx, sid)["cash_total"]
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=tot, safety_decisions={i: "ok" for i in range(len(f))})
        self.assertEqual(inventory.verify_balances(self.db, self.ctx.org_id), [])
        self.assertEqual(audit.verify_chain(self.db), [])
