"""#202 part 3: long-run proof that stock balances never drift.

Thousands of mixed operations through the real services (sales across batches, returns, same-day voids,
purchases incl. real invoice approve and cancel, supplier returns, manual changes, stock counts), cashiers
selling at the same time on separate threads, a sale whose process is killed in the middle of its
transaction, and a backup restored and worked on afterwards. A plain Python model of the expected stock per
item is kept from what each successful operation should do, independently of the database; at every
checkpoint and at the end the program's balances must equal the model, verify_balances must be empty and the
#202 integrity scan must be clean (zero drift).

Size: PHARM_LONG_RUN=<operations> (default 3000, about 10 s; run with 20000
for a multi-year night run). Seeded, so a failure reproduces. Synthetic data only.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app import config
from app.catalog import service as cat
from app.core import audit, backup, people
from app.core.errors import Conflict, DomainError
from app.db import Database
from app.inventory import adjust, integrity, service as inv
from app.inventory.counting import Counting
from app.procurement import returns
from app.purchasing.service import Purchasing
from app.sales.service import Sales
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import OWNER_PW, DBTestCase

ROOT = Path(__file__).resolve().parent.parent
OPS = int(os.environ.get("PHARM_LONG_RUN", "3000"))
SEED = int(os.environ.get("PHARM_LONG_RUN_SEED", "202"))
PW = "Staff-Pass-2026"
FUT = (date.today() + timedelta(days=700)).strftime("%m/%Y")

# The child process of the crash test: it opens the same file, starts completing a sale and stops for good
# inside the transaction, after the first stock movement was written; the parent then kills it.
CRASH_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from app.core import accounts, security
from app.db import Database
from app.inventory import service as inv
from app.sales.service import Sales
db = Database(sys.argv[2])
ctx = accounts.resolve_session(db, sys.argv[3])
s = Sales(db)
sid = s.create(ctx)
s.add_line(ctx, sid, sys.argv[4], units=int(sys.argv[5]))
s.add_line(ctx, sid, sys.argv[6], units=1)
real = inv.post_movement
def stuck(*a, **k):
    out = real(*a, **k)
    print("IN_TX " + sid, flush=True)
    time.sleep(120)
    return out
inv.post_movement = stuck
f = s.safety_check(ctx, sid)
s.complete(ctx, sid, paid_card=s.get(ctx, sid)["total"], safety_decisions={i: "ok" for i in range(len(f))})
print("FINISHED", flush=True)
"""


class Model:
    """Expected units per item, from what each successful operation must do."""

    def __init__(self):
        self.qty: dict[str, int] = {}

    def add(self, item, n):
        self.qty[item] = self.qty.get(item, 0) + n


class World:
    """One pharmacy's database plus the operations, so the same run can continue on a restored copy."""

    def __init__(self, case: "LongRun", db: Database, ctx, model: Model, rng: random.Random):
        self.case, self.db, self.ctx, self.model, self.rng = case, db, ctx, model, rng
        self.s = Sales(db)
        self.counting = Counting(db)
        self.sales: list[dict] = []       # completed, not voided: {id, line_id, item, left}
        self.done: dict[str, int] = {}

    def tick(self, name):
        self.done[name] = self.done.get(name, 0) + 1

    # -------------------------------------------------------------- operations
    def purchase(self, item, n):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, item, f"P{self.rng.randrange(10**6)}",
                                        (date.today() + timedelta(days=self.rng.randrange(60, 900))).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=n, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal(self.rng.randrange(500, 3000)) / 100, ref_type="purchase_invoice")
        self.model.add(item, n)
        self.tick("purchase")

    def sell(self, ctx=None, s=None, *, item, units):
        ctx, s = ctx or self.ctx, s or self.s
        sid = s.create(ctx)
        d = s.add_line(ctx, sid, item, units=units)
        f = s.safety_check(ctx, sid)
        return s.complete(ctx, sid, paid_card=d["total"], safety_decisions={i: "ok" for i in range(len(f))})

    def op_sale(self):
        item = self.rng.choice(self.case.multi + self.case.single)
        units = self.rng.randint(1, 6)
        try:
            r = self.sell(item=item, units=units)
        except Conflict:                                 # more than the shelf holds: refused, nothing moved
            self.tick("sale_refused")
            return
        self.model.add(item, -units)
        self.sales.append({"id": r["id"], "line_id": r["lines"][0]["id"], "item": item, "left": units,
                           "returned": False})
        self.tick("sale")

    def op_return(self):
        open_ = [x for x in self.sales[-200:] if x["left"]]
        if not open_:
            return
        x = self.rng.choice(open_)
        n = self.rng.randint(1, x["left"])
        self.s.return_items(self.ctx, x["id"], {x["line_id"]: n}, refund_method="card", reason="مرتجع عميل")
        x["left"] -= n
        x["returned"] = True
        self.model.add(x["item"], n)
        self.tick("return")

    def op_void(self):
        cands = [x for x in self.sales[-100:] if not x["returned"] and x["left"]]
        if not cands:
            return
        x = self.rng.choice(cands)
        self.s.void(self.ctx, x["id"], "خطأ في الفاتورة")
        self.model.add(x["item"], x["left"])
        x["left"] = 0
        self.sales.remove(x)
        self.tick("void")

    def op_supplier_return(self):
        item = self.rng.choice(self.case.multi)
        if self.model.qty.get(item, 0) < 3:
            return
        n = self.rng.randint(1, 2)
        returns.create(self.db, self.ctx, self.case.sup, [{"item_id": item, "qty_base": n, "reason": "overstock"}])
        self.model.add(item, -n)
        self.tick("supplier_return")

    def op_adjust(self):
        item = self.rng.choice(self.case.multi)
        have = self.model.qty.get(item, 0)
        if self.rng.random() < 0.5 and have >= 2:
            adjust.adjust(self.db, self.ctx, item, qty_delta=-1, reason="damaged")
            self.model.add(item, -1)
        elif have:
            target = self.rng.randint(0, have)
            if target == have:
                return
            adjust.adjust(self.db, self.ctx, item, set_to=target, reason="expired")
            self.model.qty[item] = target
        else:
            return
        self.tick("adjust")

    def op_found(self):
        item = self.rng.choice(self.case.multi)
        n = self.rng.randint(1, 3)
        adjust.adjust(self.db, self.ctx, item, qty_delta=n, reason="found")
        self.model.add(item, n)
        self.tick("found")

    def op_count(self):
        item = self.rng.choice(self.case.single)          # one batch each: the count sets the whole item
        b = self.case.single_batch[item]
        have = self.model.qty.get(item, 0)
        counted = max(0, have + self.rng.randint(-2, 2))
        sid = self.counting.create(self.ctx, title="جرد دوري", scope="items", item_ids=[item])
        self.counting.record(self.ctx, sid, item_id=item, qty=counted, batch_id=b)
        try:
            self.counting.approve(self.ctx, sid)
        except DomainError:
            self.counting.cancel(self.ctx, sid)
            return
        self.model.qty[item] = counted
        self.tick("count")

    def op_restock(self):
        for item in self.case.single:
            if self.model.qty.get(item, 0) < 10:
                with self.db.transaction():
                    inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=40,
                                      batch_id=self.case.single_batch[item], movement_type="purchase",
                                      unit_cost=Decimal("12"), ref_type="purchase_invoice")
                self.model.add(item, 40)
        for item in self.case.multi:
            if self.model.qty.get(item, 0) < 15:
                self.purchase(item, self.rng.randint(20, 60))

    def run(self, n):
        ops = [(self.op_sale, 55), (self.op_return, 10), (self.op_void, 5), (self.op_supplier_return, 5),
               (self.op_adjust, 5), (self.op_found, 3), (self.op_count, 4), (self.op_restock, 13)]
        fns, weights = zip(*ops)
        for i in range(n):
            self.rng.choices(fns, weights)[0]()
            if i % 250 == 249:
                self.case.assert_no_drift(self, f"after {i + 1} operations")

    # -------------------------------------------------------------- truth
    def totals(self):
        return {r["item_id"]: r["q"] for r in self.db.all(
            "SELECT item_id, sum(qty) q FROM stock_balances WHERE org_id=? GROUP BY item_id", (self.ctx.org_id,))}


class LongRun(DBTestCase):
    def setUp(self):
        super().setUp()
        self.rng = random.Random(SEED)
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما")
        self.multi = [cat.create_item(self.db, self.ctx, {"name_en": f"Item M{i} 10 tab", "pack_size": 10,
                                                          "public_price": 1000 + 10 * i}) for i in range(8)]
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 4000})
        self.multi.append(self.pan)
        self.single = [cat.create_item(self.db, self.ctx, {"name_en": f"Item S{i} 20 tab", "pack_size": 2,
                                                           "public_price": 2000 + i}) for i in range(4)]
        with self.db.transaction():
            self.single_batch = {i: inv.get_or_create_batch(self.db, self.ctx, i, "S1", (date.today() + timedelta(
                days=800)).isoformat()) for i in self.single}
        self.model = Model()
        self.world = World(self, self.db, self.ctx, self.model, self.rng)
        self.world.op_restock()

    def assert_no_drift(self, w: World, step: str):
        got = {k: v for k, v in w.totals().items() if v}
        want = {k: v for k, v in w.model.qty.items() if v}
        self.assertEqual(got, want, f"stock differs from the model {step}")
        self.assertEqual(inv.verify_balances(w.db, w.ctx.org_id), [], step)
        r = integrity.scan(w.db, w.ctx.org_id)
        self.assertTrue(r["ok"], f"{step}: {r['counts']}")

    def concurrent_cashiers(self, w: World, cashiers=4, each=40):
        ctxs = []
        for k in range(cashiers):
            name = f"cashier{k}{self.rng.randrange(10**6)}"
            people.add_user(w.db, w.ctx, username=name, full_name=f"كاشير {k}", password=PW, role_code="assistant")
            ctxs.append(self.login(name, PW))
        sold: list[tuple[str, int]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        items = self.multi[:3]                      # the same few items: real contention on the same batches
        from app.sales import cash
        if not cash.open_shift_id(w.db, w.ctx):  # every store sale needs an open shift (final review P2-2)
            cash.CashDrawer(w.db).open(w.ctx, 0)

        def work(ctx, seed):
            rng = random.Random(seed)
            s = Sales(w.db)
            try:
                for _ in range(each):
                    item, units = rng.choice(items), rng.randint(1, 4)
                    try:
                        w.sell(ctx, s, item=item, units=units)
                    except Conflict:
                        continue
                    with lock:
                        sold.append((item, units))
            except BaseException as e:            # noqa: BLE001 - reported below
                errors.append(e)
        threads = [threading.Thread(target=work, args=(c, SEED + k)) for k, c in enumerate(ctxs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(300)
        self.assertEqual(errors, [])
        for item, units in sold:
            w.model.add(item, -units)
        w.done["concurrent_sale"] = w.done.get("concurrent_sale", 0) + len(sold)
        self.assertGreater(len(sold), 0)
        self.assert_no_drift(w, "after concurrent cashiers")

    def killed_mid_sale(self, w: World):
        tok = __import__("app.core.accounts", fromlist=["login"]).login(w.db, "owner", OWNER_PW)["token"]
        a, b = self.multi[0], self.multi[1]
        for it in (a, b):
            if w.model.qty.get(it, 0) < 5:
                w.purchase(it, 20)
        before = w.db.one("SELECT count(*) n FROM stock_movements")["n"]
        p = subprocess.Popen([sys.executable, "-c", CRASH_CHILD, str(ROOT), w.db.path, tok, a, "2", b],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = p.stdout.readline()
            self.assertTrue(line.startswith("IN_TX "), line + p.stderr.read() if not line else line)
        finally:
            p.kill()
            p.wait(30)
            p.stdout.close()
            p.stderr.close()
        sid = line.split()[1]
        self.assertEqual(w.db.one("SELECT status FROM sales WHERE id=?", (sid,))["status"], "draft")
        self.assertEqual(w.db.one("SELECT count(*) n FROM stock_movements")["n"], before)   # nothing half-written
        self.assert_no_drift(w, "after a sale killed mid-transaction")
        w.s.discard(w.ctx, sid)
        w.op_sale()                                          # the database is usable straight away
        w.done["killed_sale"] = 1

    def test_years_of_operations_end_with_zero_drift(self):
        w = self.world
        w.run(OPS // 3)
        self.concurrent_cashiers(w)
        self.killed_mid_sale(w)
        # a real purchase invoice approved, and another approved then cancelled
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        for no, cancel in (("LR-1", False), ("LR-2", True)):
            inv_id = p.import_file(self.ctx, excel_invoice([("PANADOL 500 MG 24 TAB", 10, 2, 40, 20, 32, 320, no,
                                                              FUT, None)], invoice_no=no), f"{no}.xlsx")
            p.approve(self.ctx, inv_id, p.get(self.ctx, inv_id)["invoice"]["version"])
            w.model.add(self.pan, 24)
            if cancel:
                p.cancel(self.ctx, inv_id, "فاتورة مكررة", p.get(self.ctx, inv_id)["invoice"]["version"])
                w.model.add(self.pan, -24)
        self.assert_no_drift(w, "after purchase invoices")
        w.run(OPS // 3)

        # backup now, keep working, then restore that backup and continue on the restored copy
        arch = backup.create(self.db, self.tmp / "u", self.tmp / "bk")
        at_backup = dict(self.model.qty)
        moves_at_backup = self.db.one("SELECT count(*) n FROM stock_movements")["n"]
        w.run(100)
        restored_path = self.tmp / "restored.db"
        backup.restore(arch, restored_path, self.tmp / "up2", self.tmp / "pre")
        rdb = Database(restored_path)
        try:
            self.assertEqual(rdb.one("SELECT count(*) n FROM stock_movements")["n"], moves_at_backup)
            rmodel = Model()
            rmodel.qty = at_backup
            tok = __import__("app.core.accounts", fromlist=["login"]).login(rdb, "owner", OWNER_PW)["token"]
            from app.core import accounts
            rctx = accounts.resolve_session(rdb, tok)
            rw = World(self, rdb, rctx, rmodel, random.Random(SEED + 1))
            self.assert_no_drift(rw, "restored backup")
            rw.run(OPS - 2 * (OPS // 3))
            self.assert_no_drift(rw, "end, on the restored copy")
            self.assertEqual(audit.verify_chain(rdb), [])
            self.assertEqual(backup.integrity_problems(rdb), [])
        finally:
            rdb.close()
        self.assert_no_drift(w, "end, original copy")
        total = sum(w.done.values()) + sum(rw.done.values())
        self.assertGreaterEqual(total, OPS * 0.8)
        for kind in ("sale", "return", "void", "supplier_return", "count", "concurrent_sale", "killed_sale"):
            self.assertTrue(w.done.get(kind) or rw.done.get(kind), f"{kind} never ran")
