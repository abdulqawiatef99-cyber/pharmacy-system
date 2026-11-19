"""#202 part 1: every code path that changes stock writes a stock movement, and the cached balance changes
only through that movement (inventory.post_movement). Two proofs:

1. Static: no module except app/inventory/service.py has SQL that writes stock_movements, stock_balances or
   item_costs, no caller asks for negative stock, and no migration adds a trigger that writes those tables.
2. Dynamic: each business operation (sale, delivery sale and its states, return, void, purchase approve and
   cancel, supplier return, stock count, manual adjustment, E-Stock opening import, refused operations) runs
   under a SQL trace. For each one, the change of every cached balance equals the sum of the new movements
   for that (branch, item, batch), every statement that wrote a stock table was one of post_movement's own,
   and the ledger still verifies. Synthetic data only.
"""
from __future__ import annotations

import io
import re
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from app import config
from app.catalog import service as cat
from app.core.errors import DomainError
from app.dataimport.service import DataImport
from app.inventory import adjust, service as inv
from app.inventory.counting import Counting
from app.procurement import returns
from app.purchasing.service import Purchasing
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parent.parent
STOCK_TABLES = ("stock_movements", "stock_balances", "item_costs")
# SQL text may be split over implicit string concatenation, so allow quotes and whitespace between the words.
_GAP = r"""(?:\s|["'])+"""
WRITE_RE = re.compile(rf"\b(?:INSERT(?:{_GAP}OR{_GAP}\w+)?{_GAP}INTO|REPLACE{_GAP}INTO|UPDATE|DELETE{_GAP}FROM)"
                      rf"{_GAP}({'|'.join(STOCK_TABLES)})\b", re.I)
ONLY_WRITER = ROOT / "app" / "inventory" / "service.py"
FUT = (date.today() + timedelta(days=500)).strftime("%m/%Y")


class StaticGuard(unittest.TestCase):
    def sources(self):
        files = list((ROOT / "app").rglob("*.py")) + [ROOT / "manage.py", ROOT / "launcher.py"]
        return [f for f in files if f.exists()]

    def test_only_the_inventory_service_writes_stock_tables(self):
        offenders = []
        for f in self.sources():
            if f == ONLY_WRITER:
                continue
            for m in WRITE_RE.finditer(f.read_text(encoding="utf-8")):
                offenders.append(f"{f.relative_to(ROOT)}: {m.group(0)!r}")
        self.assertEqual(offenders, [])

    def test_the_guard_pattern_catches_split_sql(self):
        self.assertTrue(WRITE_RE.search('db.execute("UPDATE "\n   "stock_balances SET qty=0")'))
        self.assertTrue(WRITE_RE.search("INSERT OR REPLACE INTO item_costs VALUES (1)"))
        self.assertTrue(WRITE_RE.search("delete from stock_movements"))
        self.assertFalse(WRITE_RE.search("SELECT qty FROM stock_balances"))

    def test_inventory_service_writes_only_in_post_movement_and_avg_cost(self):
        text = ONLY_WRITER.read_text(encoding="utf-8")
        funcs = re.split(r"\n(?=def )", text)
        writers = sorted({re.match(r"def (\w+)", f).group(1) for f in funcs
                          if f.startswith("def ") and WRITE_RE.search(f)})
        self.assertEqual(writers, ["_update_avg_cost", "post_movement"])

    def test_nobody_asks_for_negative_stock(self):
        hits = [str(f.relative_to(ROOT)) for f in self.sources()
                if f != ONLY_WRITER and re.search(r"allow_negative\s*=\s*True", f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_no_migration_trigger_writes_stock_tables(self):
        bad = []
        for f in sorted((ROOT / "app" / "migrations").glob("*.sql")):
            for body in re.findall(r"CREATE\s+TRIGGER.*?\bEND\s*;", f.read_text(encoding="utf-8"), re.S | re.I):
                if WRITE_RE.search(body):
                    bad.append(f.name)
        self.assertEqual(bad, [])


class LedgerWatch:
    """Context manager around one business operation."""

    def __init__(self, case: "WritePaths"):
        self.case, self.sql = case, []

    def balances(self):
        return {(r["branch_id"], r["item_id"], r["batch_key"]): r["qty"]
                for r in self.case.db.all("SELECT * FROM stock_balances")}

    def __enter__(self):
        db = self.case.db
        self.before = self.balances()
        self.rowid = db.one("SELECT coalesce(max(rowid),0) r FROM stock_movements")["r"]
        db.conn.set_trace_callback(self.sql.append)
        return self

    def __exit__(self, *exc):
        db = self.case.db
        db.conn.set_trace_callback(None)
        self.moves = [dict(r) for r in db.all("SELECT * FROM stock_movements WHERE rowid>? ORDER BY rowid",
                                               (self.rowid,))]
        after = self.balances()
        want: dict = {}
        for m in self.moves:
            k = (m["branch_id"], m["item_id"], m["batch_id"] or "")
            want[k] = want.get(k, 0) + m["qty_delta"]
        got = {k: after.get(k, 0) - self.before.get(k, 0) for k in set(after) | set(self.before)}
        got = {k: v for k, v in got.items() if v}
        want = {k: v for k, v in want.items() if v}
        self.case.assertEqual(got, want, "balance changed other than through its movements")
        writes = [s for s in self.sql if WRITE_RE.search(s)]
        allowed = ("INSERT INTO stock_movements (id, org_id, branch_id, item_id, batch_id, qty_delta,",
                   "INSERT INTO stock_balances (org_id, branch_id, item_id, batch_key, qty)",
                   "INSERT INTO item_costs (org_id, branch_id, item_id, avg_cost, updated_at)")
        self.case.assertEqual([s for s in writes if not s.startswith(allowed)], [])
        self.case.assertEqual(inv.verify_balances(db, self.case.ctx.org_id), [])
        return False

    def kinds(self):
        return sorted({(m["movement_type"], m["ref_type"]) for m in self.moves})


class WritePaths(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s, self.cd = Sales(self.db), CashDrawer(self.db)
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 4000})
        with self.db.transaction():
            self.b1 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1",
                                              (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=40, batch_id=self.b1,
                              movement_type="purchase", unit_cost=Decimal("15"))
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.cd.open(self.ctx, 0)

    def watch(self):
        return LedgerWatch(self)

    def sell(self, units=2, kind=None):
        sid = self.s.create(self.ctx)
        if kind:
            self.s.set_details(self.ctx, sid, {"kind": kind, "delivery": {"address": "شارع 1", "fee": 0}})
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        f = self.s.safety_check(self.ctx, sid)
        return self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "ok" for i in range(len(f))})

    def test_sale_return_void(self):
        with self.watch() as w:
            a = self.sell(units=5)
        self.assertEqual(w.kinds(), [("sale", "sale")])
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), -5)
        with self.watch() as w:
            self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 2}, refund_method="cash", reason="مرتجع")
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), 2)
        self.assertEqual({k[0] for k in w.kinds()}, {"sale_return"})
        b = self.sell(units=3)
        with self.watch() as w:
            self.s.void(self.ctx, b["id"], "خطأ في الفاتورة")
        self.assertEqual(w.kinds(), [("sale_return", "sale_void")])
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), 3)

    def test_drafts_and_delivery_states_do_not_move_stock(self):
        with self.watch() as w:
            sid = self.s.create(self.ctx)
            self.s.add_line(self.ctx, sid, self.pan, units=4)
            self.s.discard(self.ctx, sid)
        self.assertEqual(w.moves, [])
        with self.watch() as w:
            d = self.sell(units=2, kind="delivery")
        self.assertEqual(w.kinds(), [("sale", "sale")])
        with self.watch() as w:
            self.s.delivery_status(self.ctx, d["id"], "delivered")
            self.s.delivery_status(self.ctx, d["id"], "settled")
        self.assertEqual(w.moves, [])
        e = self.sell(units=2, kind="delivery")
        with self.watch() as w:                       # cancelled before the courier settled: stock comes back
            self.s.void(self.ctx, e["id"], "العميل لغى الطلب")
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), 2)

    def test_refused_operations_leave_nothing(self):
        with self.watch() as w:
            sid = self.s.create(self.ctx)
            with self.assertRaises(DomainError):
                self.s.add_line(self.ctx, sid, self.pan, units=999)
            with self.assertRaises(DomainError):
                adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-999, reason="lost")
            with self.assertRaises(DomainError):
                returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "qty_base": 999, "reason": "expired"}])
        self.assertEqual(w.moves, [])

    def test_purchase_approve_and_cancel(self):
        cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما")
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        inv_id = p.import_file(self.ctx, excel_invoice([
            ("PANADOL 500 MG 24 TAB", 10, 2, 40, 20, 32, 320, "NEW", FUT, None)]), "inv.xlsx")
        line = p.get(self.ctx, inv_id)["lines"][0]
        if not line.get("item_id"):
            d = p.get(self.ctx, inv_id)
            p.update_line(self.ctx, inv_id, line["id"], {"item_id": self.pan}, d["invoice"]["version"])
        d = p.get(self.ctx, inv_id)
        with self.watch() as w:
            p.approve(self.ctx, inv_id, d["invoice"]["version"])
        self.assertEqual(w.kinds(), [("purchase", "purchase_invoice")])
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), 24)
        d = p.get(self.ctx, inv_id)
        with self.watch() as w:
            p.cancel(self.ctx, inv_id, "فاتورة مكررة", d["invoice"]["version"])
        self.assertEqual(w.kinds(), [("purchase_reversal", "purchase_invoice")])
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), -24)

    def test_supplier_return_with_and_without_batch(self):
        with self.db.transaction():          # stock kept without a lot: the only stock a return without a batch takes
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=3, movement_type="opening")
        with self.watch() as w:
            returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "batch_id": self.b1, "qty_base": 2,
                                                          "reason": "expired"}])
            returns.create(self.db, self.ctx, self.sup, [{"item_id": self.pan, "qty_base": 3, "reason": "overstock"}])
        self.assertEqual(w.kinds(), [("supplier_return", "supplier_return")])
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), -5)

    def test_count_and_manual_adjustments(self):
        c = Counting(self.db)
        sid = c.create(self.ctx, title="جرد", scope="items", item_ids=[self.pan])
        c.record(self.ctx, sid, item_id=self.pan, qty=37, batch_id=self.b1)
        with self.watch() as w:
            c.approve(self.ctx, sid)
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), -3)
        self.assertEqual({m["ref_id"] for m in w.moves}, {sid})
        with self.watch() as w:
            adjust.adjust(self.db, self.ctx, self.pan, qty_delta=4, batch_id=self.b1, reason="found")
            adjust.adjust(self.db, self.ctx, self.pan, qty_delta=-1, batch_id=self.b1, reason="damaged")
            adjust.adjust(self.db, self.ctx, self.pan, set_to=10, reason="expired")
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), 10 - 37)
        self.assertEqual({m["ref_type"] for m in w.moves}, {"manual_adjust"})

    def test_estock_opening_import(self):
        def x(rows):
            wb = Workbook()
            for r in rows:
                wb.active.append(r)
            b = io.BytesIO()
            wb.save(b)
            return b.getvalue()
        di = DataImport(self.db)
        di.apply(self.ctx, di.analyze(self.ctx, x([["كود", "اسم الصنف", "سعر الجمهور", "عدد الوحدات"],
                                                   [7, "Brufen 400mg 20 tab", 30, 2]]), "items.xlsx"))
        with self.watch() as w:
            run = di.analyze(self.ctx, x([["كود", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"],
                                          [7, 6, 20, "X1", FUT]]), "stock.xlsx", kind="opening_stock")
            di.apply(self.ctx, run)
        self.assertEqual(w.kinds(), [("opening", "import_run")])
        self.assertEqual(sum(m["qty_delta"] for m in w.moves), 12)


if __name__ == "__main__":
    unittest.main()
