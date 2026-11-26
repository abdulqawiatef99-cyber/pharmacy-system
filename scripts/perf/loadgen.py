#!/usr/bin/env python3
"""Reproducible load generator: a synthetic pharmacy with years of activity (issue #113 / PERFORMANCE.md).

SYNTHETIC DATA ONLY. It never opens an existing database: the output folder must not exist yet.
Everything is written through the application's own services (purchases, sales, returns, voids,
shifts, customer and supplier ledgers, expenses, stock adjustments, transfer screenshots), so every
invariant (ledger = balances, audit hash chain, gap-free numbers) holds and `manage.py verify` passes.
A simulated clock spreads the activity over its real dates (scripts/perf/simclock.py).

  python scripts/perf/loadgen.py --out /tmp/perf --years 5 --snapshots 1,3,5
  python scripts/perf/loadgen.py --out /tmp/perf-small --days 3 --items 60 --per-day 10-20   (smoke)

Output: <out>/y<N>/data/{pharmacy.db,uploads/} for every snapshot (a consistent copy made with the
SQLite backup API at the end of year N), plus <out>/loadgen.json (parameters, counts, timings).
Same --seed => same data shape (ids and hashes differ: they contain time and randomness by design).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import struct
import sys
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

# PHARM_PROGRAM_DIR: run these scripts from a folder outside the installed program (real_years.py on the PC)
ROOT = Path(os.environ.get("PHARM_PROGRAM_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))



def use_cairo_time() -> None:
    """The pharmacy's local time for this run. Called first thing in main(), never at import: the test suite
    imports these scripts, and a time zone set at import moved every later test to Cairo time (failing the
    local-day tests near midnight)."""
    if os.name != "nt":                  # Windows C runtime misreads an IANA name in TZ; it already runs on local time
        os.environ.setdefault("TZ", "Africa/Cairo")
    if hasattr(time, "tzset"):
        time.tzset()


from simclock import SimClock, install_python, install_sql  # noqa: E402

from app.core import accounts, people, security  # noqa: E402
from app.core.errors import Conflict, ValidationFailed  # noqa: E402
from app.db import Database  # noqa: E402

security.set_cost(2 ** 10)   # synthetic users only: fast password hashing

FORMS = [("Tab", "أقراص"), ("Cap", "كبسول"), ("Syrup", "شراب"), ("Cream", "كريم"), ("Amp", "حقن"),
         ("Drops", "نقط"), ("Sachet", "أكياس"), ("Gel", "جل"), ("Spray", "بخاخ"), ("Supp", "لبوس")]
SYL = ["ra", "mo", "li", "xa", "ten", "vo", "pri", "zol", "fen", "dex", "cor", "na", "mi", "sta", "tro",
       "lex", "bu", "qui", "der", "phy", "sel", "ka", "lo", "zin", "var", "pam", "tol", "gen", "cef", "ami"]
AR = ["را", "مو", "لي", "زا", "تن", "فو", "بري", "زول", "فين", "دكس", "كور", "نا", "مي", "ستا", "ترو",
      "لكس", "بو", "كوي", "در", "في", "سل", "كا", "لو", "زين", "فار", "بام", "تول", "جين", "سيف", "امي"]
STRENGTHS = ["5mg", "10mg", "20mg", "25mg", "50mg", "100mg", "250mg", "500mg", "1g", "120mg/5ml", "1%", "2%"]


GEN_INDEX = "perf_loadgen_alloc_line"
# Generator-only indexes, dropped from every snapshot (drop_gen_indexes): rows read by sale, shift, customer or
# item on every sale, which the program's own schema finds by reading a whole table (the defect PERFORMANCE.md
# measures; docs/PERFORMANCE.md "indexes"). Without them a sale costs more with every sale before it and years of a
# large pharmacy would take days to generate. They change how fast rows are found, never which rows.
# sale_lines: an index on item_id alone is chosen by SQLite for "this item on this sale" (Sales.add_line) instead of
# the sale's own lines, and then every scan reads all the lines the item ever had; with sale_id second it is exact.
GEN_INDEXES = {
    GEN_INDEX: "sale_allocations(sale_line_id)",
    "perf_loadgen_sales_completed": "sales(org_id, branch_id, status, completed_at)",
    "perf_loadgen_sales_shift": "sales(shift_id)",
    "perf_loadgen_sale_lines_item": "sale_lines(item_id, sale_id)",   # with sale_id: see add_line below
    "perf_loadgen_sales_customer": "sales(org_id, customer_id)",
    "perf_loadgen_pinv_lines_item": "purchase_invoice_lines(org_id, item_id)",
    "perf_loadgen_sale_returns_sale": "sale_returns(sale_id)",
    # approving a purchase line asks for the item's last purchase cost without the branch, so ix_moves_item gives
    # it every movement of the pharmacy to sort: 29 ms a line after 100 days of a large pharmacy, and growing
    "perf_loadgen_moves_item_type": "stock_movements(org_id, item_id, movement_type)",
}


def create_gen_indexes(db, plain: bool = False) -> None:
    """plain: only the first one, as before (the test compares the data both ways)."""
    with db.transaction():
        for name, on in GEN_INDEXES.items():
            if not plain or name == GEN_INDEX:
                db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {on}")


def drop_gen_indexes(conn: sqlite3.Connection) -> None:
    """A snapshot keeps the program's own schema only."""
    for name in GEN_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
OWNER_PW, STAFF_PW = "Perf-Owner-2026", "Perf-Staff-2026"     # synthetic accounts of the synthetic pharmacy


class GenDatabase(Database):
    """The app's Database with the simulated clock on every connection and fsync off.
    synchronous=OFF is for generating synthetic data fast; the measured runs use the app's own settings."""

    clock: SimClock

    def _connect(self):
        conn = super()._connect()
        conn.execute("PRAGMA synchronous = OFF")
        install_sql(conn, self.clock)
        return conn


def install_fast_matching(db=None, org_id: str | None = None) -> dict:
    """Generator-only speed-up, same results: remember the catalog's fuzzy name matches.

    The safety check looks for look-alike names of every basket line (matching.fuzzy_candidates) on every check,
    and a purchase line matches its name the same way; each call scores the name against every item that shares a
    prefix, and the name index is rebuilt whenever an item row changes, so with 15,000-30,000 items that is nearly
    all of the generator's time (PERFORMANCE.md). During generation the catalog is fixed (items are made once in
    setup; no name, strength, form or status changes afterwards), so the answer for a name never changes: it is
    computed once by the program's own code and returned again. `_ratio` keeps difflib's exact value and only
    skips the work when the answer is already known (equal strings, no common character, the same pair again).
    With db and org_id every item's name is matched now, on one build of the index, and so is each item against
    its own name (what a purchase line of a known item asks).
    The program itself is not changed: the measured server (bench.py) runs without this.
    """
    from difflib import SequenceMatcher
    from app.catalog import matching
    if getattr(matching.fuzzy_candidates, "perf_memo", None) is None:
        original = matching.fuzzy_candidates
        memo: dict = {}
        seen: dict = {}
        KEEP = 12                                          # the program asks for 5 or 6

        def ratio(a: str, b: str) -> float:
            if not a or not b:
                return 0.0
            if a == b:
                return 1.0                                 # 2*len/(len+len)
            r = seen.get((a, b))
            if r is None:
                # no common character = no matching block (an English name against an Arabic one)
                r = 0.0 if set(a).isdisjoint(b) else SequenceMatcher(None, a, b).ratio()
                if len(seen) > 400_000:
                    seen.clear()
                seen[(a, b)] = r
            return r

        def fuzzy_candidates(db, org_id, raw_name, limit=5):
            if limit > KEEP:
                return original(db, org_id, raw_name, limit)
            key = (org_id, raw_name)
            hit = memo.get(key)
            if hit is None:                                # the ranked list once; every smaller limit is a prefix of it
                hit = memo[key] = tuple(original(db, org_id, raw_name, KEEP))
            return list(hit[:limit])

        own_label = matching._label_score
        labels: dict = {}

        def label_score(db, org_id, item_id, raw_name):
            # a repeat purchase is matched by its alias and then compared with the item's own name; asked through
            # the name index, that rebuilds the whole index after every approved invoice (an item row changed)
            key = (org_id, item_id, raw_name)
            hit = labels.get(key)
            if hit is None:
                hit = labels[key] = own_label(db, org_id, item_id, raw_name)
            return hit

        fuzzy_candidates.perf_memo = memo
        fuzzy_candidates.perf_labels = labels
        matching._ratio = ratio
        matching.fuzzy_candidates = fuzzy_candidates
        matching._label_score = label_score
    if db is not None:
        for r in db.all("SELECT id, coalesce(name_en, name_ar) n FROM items WHERE org_id=? AND status='active'", (org_id,)):
            matching.fuzzy_candidates(db, org_id, r["n"])
            matching._label_score(db, org_id, r["id"], r["n"])     # the name a manual purchase line of the item carries
    return matching.fuzzy_candidates.perf_memo


def ean13(n: int) -> str:
    body = f"299{n:09d}"                                   # 2xx = restricted circulation: never a real product
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - s % 10) % 10)


def synthetic_png(rng: random.Random, size: int) -> bytes:
    """A valid PNG of about `size` bytes whose payload is incompressible, like a real screenshot."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    pad = rng.randbytes(max(0, size - 80))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"perf\x00" + pad) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class Generator:
    def __init__(self, a):
        self.a = a
        self.rng = random.Random(a.seed)
        self.out = Path(a.out)
        self.work = self.out / "work" / "data"
        self.work.mkdir(parents=True)
        self.upload_dir = self.work / "uploads"
        self.days = a.days or int(round(a.years * 365))
        self.start = date.fromisoformat(a.end) - timedelta(days=self.days) if a.end else date.today() - timedelta(days=self.days)
        self.clock = SimClock(datetime(self.start.year, self.start.month, self.start.day, 8, 0).astimezone())
        GenDatabase.clock = self.clock
        install_python(self.clock)
        self.db = GenDatabase(self.work / "pharmacy.db")
        self.lo, self.hi = (int(x) for x in a.per_day.split("-"))
        self.stats = {k: 0 for k in ("sales", "sale_lines", "returns", "voids", "purchases", "purchase_lines",
                                     "shifts", "customer_payments", "supplier_payments", "expenses", "adjustments",
                                     "attachments", "attachment_bytes", "skipped_lines")}
        self.timing: dict[str, float] = {}
        self.finding_codes: set[str] = set()

    # ------------------------------------------------------------------ setup
    def setup(self):
        from app.catalog.card import ensure_default_categories
        from app.catalog.search import backfill_search_keys
        from app.finance.expenses import ensure_defaults as ex
        from app.sales.channels import ensure_defaults as ch
        db = self.db
        db.migrate()
        create_gen_indexes(db, plain=getattr(self.a, "plain", False))
        accounts.sync_system_roles(db)
        accounts.create_organization(db, name="صيدلية تجربة الأداء", owner_username="owner",
                                     owner_full_name="Owner", owner_password=OWNER_PW)
        backfill_search_keys(db)                 # what `manage.py serve` does on every start
        ensure_default_categories(db)
        ch(db)
        ex(db)
        self.owner = self.login("owner", OWNER_PW)
        self.staff = []
        for u, role in (("cashier1", "assistant"), ("cashier2", "assistant"), ("pharm1", "pharmacist")):
            people.add_user(db, self.owner, username=u, full_name=u, password=STAFF_PW, role_code=role)
            self.staff.append(self.login(u, STAFF_PW))
        from app.catalog import service as cat
        self.suppliers = [cat.create_supplier(db, self.owner, name=f"مورد تجربة {i + 1}", supplier_type="distributor"
                                              if i < 6 else "other") for i in range(self.a.suppliers)]
        from app.sales.service import Sales
        self.sales = Sales(db)
        self.customers = [self.sales.create_customer(self.owner, name=f"عميل تجربة {i + 1}",
                                                     phone=f"010{i:08d}", credit_limit=self.a.credit_limit)
                          for i in range(self.a.customers)]
        self.credit_customers = self.customers[: max(1, len(self.customers) // 5)]
        self.make_items()
        self.channels = {r["kind"]: r["id"] for r in db.all(
            "SELECT kind, id FROM payment_channels WHERE org_id=? AND is_active=1 ORDER BY sort", (self.owner.org_id,))}
        self.exp_cats = [r["id"] for r in db.all("SELECT id FROM expense_categories WHERE org_id=?", (self.owner.org_id,))]

    def login(self, u, pw):
        tok = accounts.login(self.db, u, pw)["token"]
        return accounts.resolve_session(self.db, tok)

    def make_items(self):
        from app.catalog import service as cat
        rng = self.rng
        self.items = []
        used: set[str] = set()
        for i in range(self.a.items):
            f_en, f_ar = rng.choice(FORMS)
            while True:                                   # a unique brand name of 2-3 syllables, like a real catalog
                k = [rng.randrange(len(SYL)) for _ in range(rng.choice([2, 3, 3]))]
                brand = "".join(SYL[j] for j in k)
                strength = rng.choice(STRENGTHS)
                if (brand, strength) not in used:
                    used.add((brand, strength))
                    break
            en = f"{brand.capitalize()} {strength} {f_en}"
            ar = f"{''.join(AR[j] for j in k)} {strength} {f_ar}"
            pack = rng.choice([1, 1, 1, 2, 3, 10, 20, 30]) if f_en in ("Tab", "Cap") else 1
            price = rng.choice([800, 1500, 2500, 3500, 4800, 6500, 9000, 12500, 18000, 26000, 45000, 90000])
            iid = cat.create_item(self.db, self.owner, {"name_en": en, "name_ar": ar, "pack_size": pack,
                                                        "public_price": price}, barcodes=[ean13(i)], force=True)
            self.items.append({"id": iid, "pack": pack, "price": price, "stock": 0,
                               "supplier": self.suppliers[i % len(self.suppliers)]})
        order = list(range(len(self.items)))
        rng.shuffle(order)
        self.set_demand(order)

    def set_demand(self, order: list[int]):
        """Demand: a few items sell every day, most rarely (Zipf-like); order[0] is the best seller."""
        self.weights = [0.0] * len(self.items)
        for rank, idx in enumerate(order):
            self.weights[idx] = 1.0 / (rank + 1) ** 0.9
        tot = sum(self.weights)
        lines_per_day = (self.lo + self.hi) / 2 * 3.5
        for i, it in enumerate(self.items):
            it["daily"] = self.weights[i] / tot * lines_per_day * 1.3        # packs per day
        self.cum = []
        acc = 0.0
        for w in self.weights:
            acc += w
            self.cum.append(acc)

    def pick_item(self) -> dict:
        import bisect
        return self.items[min(bisect.bisect_left(self.cum, self.rng.random() * self.cum[-1]), len(self.items) - 1)]

    # ------------------------------------------------------------------ purchases
    def purchase(self, day: date, supplier: str, lines: list[tuple[dict, int]]):
        from app.purchasing.service import Purchasing
        p = Purchasing(self.db)
        ctx = self.owner
        inv = p.create_manual(ctx, supplier_id=supplier)
        ver = p.get(ctx, inv, with_candidates=False)["invoice"]["version"]
        self.stats["purchases"] += 1
        p.update_header(ctx, inv, {"invoice_no": f"P{day:%Y%m%d}-{self.stats['purchases']}",
                                   "invoice_date": day.isoformat()}, ver)
        for it, packs in lines:
            ver = self.db.one("SELECT version FROM purchase_invoices WHERE id=?", (inv,))["version"]
            net = it["price"] * self.rng.choice([75, 78, 80, 82, 85]) // 100
            exp = day + timedelta(days=self.rng.randint(240, 1000))
            p.add_line(ctx, inv, {"item_id": it["id"], "qty": packs, "bonus_qty": 0, "unit_net_price": net,
                                  "public_price": it["price"], "line_total": packs * net,
                                  "batch_no": f"B{day:%y%m}{self.rng.randint(100, 999)}",
                                  "expiry_date": exp.isoformat()}, ver)
            it["stock"] += packs * it["pack"]
            self.stats["purchase_lines"] += 1
        approve_with_reasons(p, ctx, inv)

    def restock(self, day: date, initial: bool = False):
        by_sup: dict[str, list] = {}
        for it in self.items:
            need = max(1, round(it["daily"] * 21))                  # three weeks of demand
            if initial or it["stock"] < max(1, round(it["daily"] * 7)) * it["pack"]:
                if initial or self.rng.random() < 0.5:              # not every item is reordered at once
                    by_sup.setdefault(it["supplier"], []).append((it, need + (1 if initial else 0)))
        for sup, lines in by_sup.items():
            for k in range(0, len(lines), 80):                      # one invoice per 80 lines
                self.purchase(day, sup, lines[k:k + 80])

    # ------------------------------------------------------------------ one day
    def run_day(self, day: date, recent: list):
        from app.sales.cash import CashDrawer
        from app.procurement import suppliers as sup
        from app.finance import expenses
        from app.inventory import adjust
        from app.sales import attachments as att
        rng = self.rng
        drawer = CashDrawer(self.db)
        self.sales.today = day
        self.clock.set_local(day, 8, 30)
        if day.weekday() in (0, 3):
            self.restock(day)
        n_sales = rng.randint(self.lo, self.hi)
        for shift_no, (h0, h1, who) in enumerate(((9, 16, self.staff[0]), (16, 24, self.staff[1]))):
            self.clock.set_local(day, h0, 0)
            drawer.open(who, 50000)
            self.stats["shifts"] += 1
            share = n_sales // 2 if shift_no == 0 else n_sales - n_sales // 2
            span = (h1 - h0) * 3600 - 120
            for _ in range(share):
                self.clock.advance(span / max(share, 1))
                ctx = who if rng.random() < 0.8 else self.staff[2]
                self.one_sale(ctx, day, recent, att)
            # returns of recent invoices (about 2 % of sales)
            for _ in range(sum(1 for _ in range(share) if rng.random() < 0.02)):
                if recent:
                    self.one_return(self.owner, recent)
            if rng.random() < 0.15:
                expenses.record(self.db, who, category_id=rng.choice(self.exp_cats), amount=rng.choice([1500, 3000, 5000]),
                                paid_from="drawer", note="مصروف تجربة")
                self.stats["expenses"] += 1
            if h1 == 24:
                self.clock.set_local(day, 23, 59, 30)
            else:
                self.clock.set_local(day, h1, 0)
            drawer.close(who, drawer._expected(self.db.one(
                "SELECT id FROM cash_shifts WHERE status='open' AND org_id=?", (who.org_id,))["id"]))
        # ledgers: customers pay on Saturdays, suppliers at the start of the month
        self.clock.set_local(day, 23, 59, 40)
        if day.weekday() == 5:
            for c in self.credit_customers:
                bal = self.sales.balance(self.owner, c)
                if bal > 0 and rng.random() < 0.6:
                    self.sales.customer_payment(self.owner, c, bal, method="card")
                    self.stats["customer_payments"] += 1
        if day.day == 1:
            for s in self.suppliers:
                bal = sup.account(self.db, self.owner, s)["balance"]
                if bal > 0:
                    sup.pay(self.db, self.owner, s, bal * 9 // 10, "bank")
                    self.stats["supplier_payments"] += 1
        if rng.random() < 0.3:
            it = self.pick_item()
            if it["stock"] > 0:
                try:
                    adjust.adjust(self.db, self.owner, it["id"], qty_delta=-1, reason="damaged", note="تالف تجربة")
                    it["stock"] -= 1
                    self.stats["adjustments"] += 1
                except Conflict:
                    pass

    def one_sale(self, ctx, day, recent, att):
        rng = self.rng
        cust = None
        if rng.random() < 0.08:
            cust = rng.choice(self.customers)
        sid = self.sales.create(ctx, cust)
        draft = None
        for _ in range(rng.choice([1, 1, 2, 2, 3, 3, 4, 5, 6])):
            it = self.pick_item()
            units = 0
            packs = 1 if rng.random() < 0.85 else 2
            if it["pack"] > 1 and rng.random() < 0.3:
                packs, units = 0, rng.randint(1, it["pack"] - 1)
            qty = packs * it["pack"] + units
            if it["stock"] < qty:
                self.stats["skipped_lines"] += 1
                continue
            try:
                draft = self.sales.add_line(ctx, sid, it["id"], packs=packs, units=units)
                it["stock"] -= qty
            except Conflict as e:
                self.stats["skipped_lines"] += 1
                # the program sells unexpired stock only: follow what it says is sellable, so restock() reorders
                # an item whose remaining batches expired instead of dropping it from the workload
                avail = (getattr(e, "details", None) or {}).get("available")
                if isinstance(avail, int):
                    it["stock"] = min(it["stock"], avail)
        if draft is None:
            self.sales.discard(ctx, sid)
            return
        total = draft["total"]
        r = rng.random()
        # a credit sale over the remaining limit is refused (credit_limit), so it is paid in cash instead
        if (cust in self.credit_customers and r < 0.6
                and self.sales.balance(ctx, cust) + total <= self.a.credit_limit):
            pay = [{"method": "credit", "amount": total}]
        elif r < 0.12:
            pay = [{"method": "card", "amount": total, "channel_id": self.channels.get("card")}]
        elif r < 0.12 + self.a.transfer_share:
            pay = [{"method": "transfer", "amount": total, "channel_id": self.channels.get("transfer")}]
        else:
            pay = [{"method": "cash", "amount": total}]
        findings = self.sales.safety_check(ctx, sid)            # what the cashier screen asks before completing
        if findings:
            self.stats["safety_findings"] = self.stats.get("safety_findings", 0) + len(findings)
            self.finding_codes.update(f.code for f in findings)
        done = self.sales.complete(ctx, sid, payments=pay, version=draft["version"], request_id=f"perf-{sid}",
                                   safety_decisions={i: "مراجعة تجربة الأداء" for i in range(len(findings))})
        self.stats["sales"] += 1
        self.stats["sale_lines"] += len(done["lines"])
        if pay[0]["method"] == "transfer" and self.a.attachment_kb > 0:
            data = synthetic_png(rng, int(self.a.attachment_kb * 1024 * rng.uniform(0.6, 1.4)))
            att.attach(self.db, self.upload_dir, ctx, sid, data, "transfer.png")
            self.stats["attachments"] += 1
            self.stats["attachment_bytes"] += len(data)
        if rng.random() < 0.003:
            self.sales.void(self.owner, sid, "إلغاء تجربة")
            self.stats["voids"] += 1
            self._restore_stock(done)
            return
        recent.append(done)
        if len(recent) > 400:
            del recent[:100]

    def _restore_stock(self, sale):
        idx = {it["id"]: it for it in self.items}
        for l in sale["lines"]:
            idx[l["item_id"]]["stock"] += l["qty_base"]

    def one_return(self, ctx, recent):
        s = recent.pop(self.rng.randrange(len(recent)))
        line = self.rng.choice(s["lines"])
        method = "credit" if s.get("on_credit") else "card" if s.get("paid_card") else "cash"
        try:
            self.sales.return_items(ctx, s["id"], {line["id"]: 1}, refund_method=method, reason="مرتجع تجربة")
        except (Conflict, ValidationFailed):
            return
        next(it for it in self.items if it["id"] == line["item_id"])["stock"] += 1
        self.stats["returns"] += 1

    # ------------------------------------------------------------------ snapshots
    def snapshot(self, label: str):
        dest = self.out / label / "data"
        dest.mkdir(parents=True)
        self.db.backup_to(dest / "pharmacy.db")
        snap = sqlite3.connect(dest / "pharmacy.db")     # the snapshot keeps the program's own schema only
        drop_gen_indexes(snap)
        snap.execute("VACUUM")
        snap.close()
        if self.upload_dir.exists():
            shutil.copytree(self.upload_dir, dest / "uploads")
        counts = {t: self.db.one(f"SELECT count(*) c FROM {t}")["c"] for t in
                  ("items", "batches", "sales", "sale_lines", "sale_returns", "purchase_invoices",
                   "purchase_invoice_lines", "stock_movements", "audit_log", "customer_ledger", "supplier_ledger",
                   "cash_shifts", "cash_movements", "source_files")}
        return {"label": label, "day": self.clock.now_utc.astimezone().date().isoformat(), "counts": counts,
                "stats": dict(self.stats)}

    def run(self):
        t0 = time.perf_counter()
        self.setup()
        if not getattr(self.a, "plain", False):
            install_fast_matching(self.db, self.owner.org_id)   # after setup: the catalog is complete and stays as it is
        self.restock(self.start, initial=True)
        self.timing["setup_s"] = round(time.perf_counter() - t0, 1)
        snaps = {int(round(float(y) * 365)): f"y{y}" for y in self.a.snapshots.split(",") if y} if not self.a.days else {}
        if self.a.days:
            snaps[self.days] = "final"
        results = []
        recent: list = []
        t1 = time.perf_counter()
        for d in range(1, self.days + 1):
            day = self.start + timedelta(days=d)
            self.run_day(day, recent)
            if d % 30 == 0 and not self.a.quiet:
                rate = d / (time.perf_counter() - t1)
                print(f"day {d}/{self.days} {day} sales={self.stats['sales']} ({rate:.2f} days/s)", flush=True)
            if d in snaps:
                results.append(self.snapshot(snaps[d]))
        self.timing["days_s"] = round(time.perf_counter() - t1, 1)
        self.db.close()
        shutil.rmtree(self.out / "work")
        info = {"finding_codes": sorted(self.finding_codes), "params": vars(self.a), "start": self.start.isoformat(), "snapshots": results, "timing": self.timing}
        (self.out / "loadgen.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
        return info


def main(argv=None):
    use_cairo_time()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="new folder (must not exist)")
    ap.add_argument("--years", type=float, default=1.0)
    ap.add_argument("--days", type=int, default=0, help="overrides --years (smoke runs)")
    ap.add_argument("--snapshots", default="1", help="years at which to copy the database, e.g. 1,3,5")
    ap.add_argument("--end", default="", help="last simulated day (default: today)")
    ap.add_argument("--items", type=int, default=4000)
    ap.add_argument("--suppliers", type=int, default=12)
    ap.add_argument("--customers", type=int, default=300)
    ap.add_argument("--credit-limit", type=int, default=500000, help="every synthetic customer's credit limit, piastres")
    ap.add_argument("--per-day", default="150-300", help="sales per day, min-max")
    ap.add_argument("--transfer-share", type=float, default=0.02, help="share of sales paid by transfer with a screenshot")
    ap.add_argument("--attachment-kb", type=float, default=60, help="average transfer screenshot size")
    ap.add_argument("--seed", type=int, default=113)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--plain", action="store_true", help="without the generator's speed-ups (remembered name matches,"
                    " extra generator-only indexes): slow; the test compares the data both ways")
    a = ap.parse_args(argv)
    if Path(a.out).exists():
        sys.exit(f"{a.out} exists; the generator only writes to a new folder")
    info = Generator(a).run()
    print(json.dumps(info["snapshots"][-1]["counts"] if info["snapshots"] else {}, indent=1))
    return info


def approve_with_reasons(p, ctx, inv):
    """Record reasons for the generator's synthetic purchase quantities and prices before approval."""
    from app.purchasing.validation import UNUSUAL

    guard_codes = {"short_expiry", "slow_before_expiry"} | UNUSUAL
    d = p.get(ctx, inv, with_candidates=False)
    for i in d["issues"]:
        if i["code"] in guard_codes and not i["acknowledged"]:
            d = p.acknowledge(ctx, inv, i["code"], "بيانات تجربة: حالة شراء متفق عليها",
                              i["line_id"], d["invoice"]["version"])
    return p.approve(ctx, inv, d["invoice"]["version"])


if __name__ == "__main__":
    main()

