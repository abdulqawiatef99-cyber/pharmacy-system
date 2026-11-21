#!/usr/bin/env python3
"""Years of synthetic activity on top of a COPY of the pharmacy's own database, then timings (#253).

Runs ON the pharmacy PC. The live database is opened read-only once, copied with SQLite's backup API to a
temporary work folder on the same PC, and never touched again. Everything after that happens on the copy:

  1. setup on the copy: the owner's password is replaced (copy only) so the benchmark can log in; any open
     shift is closed; synthetic cashiers, suppliers and customers are added; demand is spread over the real
     catalog (items that have stock sell most);
  2. the clock moves forward from today and loadgen.Generator writes days of purchases, sales, returns,
     voids, shifts, ledgers, expenses and adjustments through the program's own services;
  3. at each snapshot (default 1, 3 and 5 years) a consistent copy is measured with bench.py's code:
     start-up, search, the cashier flow, every report over short and long ranges, a long report and a backup
     while the cashier keeps scanning, and backup / verify / restore;
  4. the work folder is deleted.

Only timings and row counts are written (never names, barcodes, prices or users), to --out-txt and the same
numbers as JSON next to it. The text file is rewritten after every step, so a stop loses nothing measured.

  python scripts/perf/real_years.py --out-txt "%USERPROFILE%\\PHARMACY-TEST\\real-years-results.txt" --stop-at 08:30
  python scripts/perf/real_years.py --live-data /tmp/fake/data --out-txt /tmp/r.txt --days 3,6 --items 60 --per-day 8-12

Pass limits (owner, 15/11): cashier actions < 300 ms, any report < 5 s, nothing blocks other users.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

# PHARM_PROGRAM_DIR: run these scripts from a folder outside the installed program (real_years.py on the PC)
ROOT = Path(os.environ.get("PHARM_PROGRAM_DIR") or Path(__file__).resolve().parents[2])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))


def use_cairo_time() -> None:
    """The pharmacy's local time for this run. Called first thing in main(), never at import: the test suite
    imports these scripts, and a time zone set at import moved every later test to Cairo time (failing the
    local-day tests near midnight)."""
    if os.name != "nt":                  # Windows C runtime misreads an IANA name in TZ; it already runs on local time
        os.environ.setdefault("TZ", "Africa/Cairo")
    if hasattr(time, "tzset"):
        time.tzset()


CASHIER_MS = 300
REPORT_MS = 5000
STAFF_PREFIX = "perftest_"


# ------------------------------------------------------------------------------------------ Windows
def keep_awake() -> None:
    """The PC must not sleep overnight; run below normal priority so anything the owner opens comes first."""
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    BELOW_NORMAL = 0x00004000
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)


def free_bytes(p: Path) -> int:
    return shutil.disk_usage(p).free


# ------------------------------------------------------------------------------------------ report file
class Report:
    """Timings only. Rewritten after every step."""

    title = "REAL-YEARS PERFORMANCE TEST (#253) - timings only, synthetic activity on a copy of the database"

    def __init__(self, txt: Path):
        self.txt = txt
        self.json = txt.with_suffix(".json")
        self.data: dict = {"started": datetime.now().isoformat(timespec="seconds"), "status": "running",
                           "progress": "", "notes": [], "snapshots": []}
        txt.parent.mkdir(parents=True, exist_ok=True)

    def note(self, s: str):
        self.data["notes"].append(s)
        self.write()

    def progress(self, s: str):
        self.data["progress"] = s
        self.write()

    def write(self):
        d = self.data
        L = [self.title,
             f"started {d['started']}   status {d['status']}   updated {datetime.now().isoformat(timespec='seconds')}",
             f"limits: cashier < {CASHIER_MS} ms, report < {REPORT_MS / 1000:.0f} s, nothing blocks other users", ""]
        if d.get("plan"):
            L.append("plan: " + json.dumps(d["plan"], ensure_ascii=False))
        if d.get("base"):
            L.append("copy of the live database: " + json.dumps(d["base"], ensure_ascii=False))
        if d["progress"]:
            L.append("progress: " + d["progress"])
        for n in d["notes"]:
            L.append("note: " + n)
        for s in d["snapshots"]:
            L += ["", f"===== {s['label']} ({s.get('days', '?')} days of activity, generated in {s.get('gen_s', '?')} s) ====="]
            L += format_snapshot(s)
        if d.get("summary"):
            L += ["", "===== SUMMARY ====="] + d["summary"]
        tmp = self.txt.with_name(self.txt.name + ".tmp")
        tmp.write_text("\n".join(L) + "\n", encoding="utf-8")
        os.replace(tmp, self.txt)
        tmpj = self.json.with_name(self.json.name + ".tmp")
        tmpj.write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        os.replace(tmpj, self.json)


def mark(ms, limit) -> str:
    return "" if ms is None else ("PASS" if ms < limit else "SLOW")


def format_snapshot(s: dict) -> list[str]:
    L = []
    if s.get("error"):
        L.append("ERROR: " + s["error"])
    if s.get("counts"):
        L.append("rows: " + ", ".join(f"{k} {v}" for k, v in s["counts"].items()))
    if s.get("sizes"):
        z = s["sizes"]
        L.append(f"database {z['db_bytes'] / 1e6:.0f} MB")
    if s.get("stock_check"):
        k = s["stock_check"]
        L.append(f"stock check: {'all balances match' if k['ok'] else 'DIFFERENCES ' + str(k['counts'])}"
                 f" ({k['movements']} movements, {k['balances']} balances, {k['seconds']} s)")
    st = s.get("startup") or {}
    if st:
        L.append(f"start-up until it answers: {st.get('health_s')} s (second start {st.get('health_warm_s')} s)")
    for name, r in (s.get("cashier") or {}).items():
        if isinstance(r, dict):
            L.append(f"cashier  {name:<38} median {r['median_ms']:>8} ms  max {r['max_ms']:>8} ms  {mark(r['max_ms'], CASHIER_MS)}")
    if (s.get("cashier") or {}).get("errors"):
        L.append(f"cashier  refused steps: {s['cashier']['errors']}")
    for name, r in (s.get("requests") or {}).items():
        lim = CASHIER_MS if name in CASHIER_GETS else REPORT_MS
        kind = "cashier " if lim == CASHIER_MS else "report  "
        L.append(f"{kind} {name:<38} median {r['median_ms']:>8} ms  max {r['max_ms']:>8} ms  {mark(r['max_ms'], lim)}"
                 + ("" if r.get("status") == 200 else f"  HTTP {r.get('status')}"))
    for key, title in (("during_backup", "scanning while a backup runs"), ("during_report", "scanning while an all-years report runs")):
        b = s.get(key) or {}
        if b and not b.get("scans"):
            L.append(f"blocking {title:<38} not measured: it finished before the first scan ({b.get('backup_s', b.get('busy_s'))} s)")
        elif b:
            L.append(f"blocking {title:<38} median {b.get('add_line_median_ms')} ms  max {b.get('add_line_max_ms')} ms"
                     f"  ({b.get('scans')} scans, {b.get('busy_s')} s)  {mark(b.get('add_line_max_ms'), CASHIER_MS)}")
    pu = s.get("purchase") or {}
    if pu.get("error"):
        L.append(f"purchase invoice: {pu['error']}")
    if pu.get("add line"):
        r = pu["add line"]
        L.append(f"purchase add line ({PURCHASE_LINES} lines){'':<17} median {r['median_ms']:>8} ms  max {r['max_ms']:>8} ms  {mark(r['max_ms'], CASHIER_MS)}")
    if pu.get("approve"):
        b = pu["approve"]
        ms = b.get("busy_s", 0) * 1000
        L.append(f"purchase approve ({PURCHASE_LINES} lines){'':<18} {b.get('busy_s')} s  {mark(ms, REPORT_MS)}"
                 + ("" if b.get("status") == 200 else f"  HTTP {b.get('status')}"))
        L.append(f"blocking scanning while it is approved{'':<8} median {b.get('add_line_median_ms')} ms  max {b.get('add_line_max_ms')} ms"
                 f"  ({b.get('scans')} scans)  {mark(b.get('add_line_max_ms'), CASHIER_MS)}")
    for name, b in (s.get("during_slow") or {}).items():
        L.append(f"blocking scanning during: {name:<25} median {b.get('add_line_median_ms')} ms  max {b.get('add_line_max_ms')} ms"
                 f"  ({b.get('scans')} scans, {b.get('busy_s')} s)  {mark(b.get('add_line_max_ms'), CASHIER_MS)}")
    bk = s.get("backup") or {}
    if bk:
        L.append(f"backup   create {bk.get('create_s')} s, check {bk.get('quick_check_s')} s, verify {bk.get('verify_s')} s,"
                 f" restore {bk.get('restore_s')} s, file {bk.get('backup_bytes', 0) / 1e6:.0f} MB,"
                 f" problems {len(bk.get('verify_problems') or []) + len(bk.get('quick_check_problems') or [])}")
    return L


REQUEST_TIMEOUT = 180                                             # seconds a report may take before it is given up
LAST = {"unsold items (month)", "unsold items (all years)"}      # see measure()
SLOWEST = 3          # how many of the slowest requests get the "cashier keeps scanning meanwhile" probe
CASHIER_GETS = {"search name (4 letters)", "search barcode", "POS quick buttons"}


# ------------------------------------------------------------------------------------------ copy
def safe_error(e: BaseException) -> str:
    """Error type and where it happened only: program messages can carry the pharmacy's name or item names."""
    where = " <- ".join(f"{Path(fr.filename).name}:{fr.lineno}" for fr in traceback.extract_tb(e.__traceback__)[-4:])
    own = isinstance(e, SystemExit) and isinstance(e.code, str)      # this script's own messages hold no data
    return f"{type(e).__name__}" + (f": {e.code}" if own else "") + (f" at {where}" if where else "")


def copy_live(live_db: Path, dest: Path) -> None:
    """One read-only open of the live database; a consistent copy even while the program is running."""
    src = sqlite3.connect(f"file:{live_db.as_posix()}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


# ------------------------------------------------------------------------------------------ generator
_CLOCK: list = []

# Pharmacy sizes (owner, 15/11: the test must cover the smallest to the largest pharmacy).
# per_day = sales a day, items = catalog items that sell, staff = cashiers taking turns.
PROFILES = {
    "small":  {"per_day": "30-60",     "items": 2000,  "customers": 100,  "suppliers": 6},
    "medium": {"per_day": "150-300",   "items": 4000,  "customers": 300,  "suppliers": 12},
    "large":  {"per_day": "800-1200",  "items": 15000, "customers": 1500, "suppliers": 25},
    "huge":   {"per_day": "2500-3500", "items": 30000, "customers": 5000, "suppliers": 40},
    # owner 17/11: some pharmacies sell over a million EGP a day; the program is sold to every size
    "giant":  {"per_day": "6000-9000", "items": 45000, "customers": 10000, "suppliers": 60},
}


def make_generator(a, work: Path):
    import loadgen
    from loadgen import GenDatabase, Generator, OWNER_PW, STAFF_PW
    from simclock import SimClock, install_python
    from app.core import accounts, people
    from app.core.errors import Conflict, Forbidden, ValidationFailed

    class RealGenerator(Generator):
        """loadgen's day simulation on the copied real database instead of a new synthetic pharmacy."""

        def __init__(self, a, work: Path):          # noqa: no super(): it would create a new empty database
            self.a = a
            self.rng = __import__("random").Random(a.seed)
            self.out = work
            self.work = work / "gen" / "data"
            self.upload_dir = self.work / "uploads"
            self.start = date.today()
            start = datetime(self.start.year, self.start.month, self.start.day, 8, 0).astimezone()
            if _CLOCK:                                   # one clock per process: install_python patches modules once
                self.clock = _CLOCK[0]
                self.clock.now_utc = SimClock(start).now_utc
            else:
                self.clock = SimClock(start)
                _CLOCK.append(self.clock)
                install_python(self.clock)
            GenDatabase.clock = self.clock
            self.db = GenDatabase(self.work / "pharmacy.db")
            self.lo, self.hi = (int(x) for x in a.per_day.split("-"))
            self.stats = {k: 0 for k in ("sales", "sale_lines", "returns", "voids", "purchases", "purchase_lines",
                                         "shifts", "customer_payments", "supplier_payments", "expenses", "adjustments",
                                         "attachments", "attachment_bytes", "skipped_lines", "refused_sales")}
            self.timing = {}
            self.finding_codes = set()

        def setup(self):
            from app.catalog import service as cat
            from app.catalog.card import ensure_default_categories
            from app.catalog.search import backfill_search_keys
            from app.finance.expenses import ensure_defaults as ex
            from app.sales.cash import CashDrawer
            from app.sales.channels import ensure_defaults as ch
            from app.sales.service import Sales
            db = self.db
            db.migrate()
            loadgen.create_gen_indexes(db)
            accounts.sync_system_roles(db)
            org = db.one("SELECT o.id FROM organizations o LEFT JOIN items i ON i.org_id=o.id WHERE o.status='active'"
                         " GROUP BY o.id ORDER BY count(i.id) DESC LIMIT 1")
            if not org:
                raise SystemExit("no active pharmacy in the database")
            owner = db.one("SELECT u.id, u.username FROM memberships m JOIN roles r ON r.id=m.role_id JOIN users u ON u.id=m.user_id"
                           " WHERE m.org_id=? AND r.code='owner' AND m.is_active=1 LIMIT 1", (org["id"],))
            if not owner:
                raise SystemExit("the pharmacy has no active owner")
            self.owner_username = owner["username"]            # kept in memory only, never written out
            with db.transaction():                              # a session for the owner without his password (copy only)
                tok = accounts._new_session(db, owner["id"], org["id"], None, "real-years", "auth.login")["token"]
            ctx = accounts.resolve_session(db, tok)
            people.reset_password(db, ctx, owner["id"], OWNER_PW)   # the copy's owner password, for the benchmark login
            backfill_search_keys(db)
            ensure_default_categories(db)
            ch(db)
            ex(db)
            self.owner = self.login(self.owner_username, OWNER_PW)
            drawer = CashDrawer(db)
            for s in db.all("SELECT id FROM cash_shifts WHERE org_id=? AND status='open'", (org["id"],)):
                drawer.close(self.owner, drawer._expected(s["id"]))
            self.staff = []
            for u, role in (("cashier1", "assistant"), ("cashier2", "assistant"), ("pharm1", "pharmacist")):
                name = STAFF_PREFIX + u
                if not db.one("SELECT 1 FROM users WHERE username=?", (name,)):
                    people.add_user(db, self.owner, username=name, full_name=name, password=STAFF_PW, role_code=role)
                self.staff.append(self.login(name, STAFF_PW))
            self.suppliers = [cat.create_supplier(db, self.owner, name=f"مورد تجربة سنين {i + 1}",
                                                  supplier_type="distributor" if i < 6 else "other")
                              for i in range(self.a.suppliers)]
            self.sales = Sales(db)
            self.customers = [self.sales.create_customer(self.owner, name=f"عميل تجربة سنين {i + 1}",
                                                         phone=f"019{i:08d}", credit_limit=self.a.credit_limit)
                              for i in range(self.a.customers)]
            self.credit_customers = self.customers[: max(1, len(self.customers) // 5)]
            self.pick_real_items()
            self.channels = {r["kind"]: r["id"] for r in db.all(
                "SELECT kind, id FROM payment_channels WHERE org_id=? AND is_active=1 ORDER BY sort", (self.owner.org_id,))}
            self.exp_cats = [r["id"] for r in db.all("SELECT id FROM expense_categories WHERE org_id=?", (self.owner.org_id,))]

        def pick_real_items(self):
            rows = self.db.all(
                "SELECT i.id, i.pack_size, i.public_price, coalesce((SELECT sum(qty) FROM stock_balances sb"
                " WHERE sb.item_id=i.id), 0) AS stock FROM items i WHERE i.org_id=? AND i.status='active'"
                " AND i.is_service=0 AND i.hidden=0 AND i.public_price > 0 ORDER BY i.id", (self.owner.org_id,))
            stocked = [r for r in rows if r["stock"] > 0]
            rest = [r for r in rows if r["stock"] <= 0]
            self.rng.shuffle(stocked)
            self.rng.shuffle(rest)
            chosen = (stocked + rest)[: self.a.items]          # items in stock today sell most
            if len(chosen) < 10:
                raise SystemExit(f"only {len(chosen)} sellable items in the catalog")
            self.items = [{"id": r["id"], "pack": r["pack_size"], "price": r["public_price"], "stock": 0,
                           "supplier": self.suppliers[k % len(self.suppliers)]} for k, r in enumerate(chosen)]
            self.base_counts = {"catalog_items": len(rows), "items_in_stock": len(stocked), "items_used": len(chosen)}
            self.set_demand(list(range(len(self.items))))

        def purchase(self, day: date, supplier: str, lines: list):
            # loadgen's purchase, but a real item the program refuses on a purchase line is skipped, not fatal
            from app.purchasing.service import Purchasing
            p = Purchasing(self.db)
            ctx = self.owner
            inv = p.create_manual(ctx, supplier_id=supplier)
            ver = p.get(ctx, inv, with_candidates=False)["invoice"]["version"]
            self.stats["purchases"] += 1
            p.update_header(ctx, inv, {"invoice_no": f"RY{day:%Y%m%d}-{self.stats['purchases']}",
                                       "invoice_date": day.isoformat()}, ver)
            added = 0
            for it, packs in lines:
                ver = self.db.one("SELECT version FROM purchase_invoices WHERE id=?", (inv,))["version"]
                net = max(1, it["price"] * self.rng.choice([75, 78, 80, 82, 85]) // 100)
                exp = day + timedelta(days=self.rng.randint(240, 1000))
                try:
                    p.add_line(ctx, inv, {"item_id": it["id"], "qty": packs, "bonus_qty": 0, "unit_net_price": net,
                                          "public_price": it["price"], "line_total": packs * net,
                                          "batch_no": f"RY{day:%y%m}{self.rng.randint(100, 999)}",
                                          "expiry_date": exp.isoformat()}, ver)
                except (Conflict, ValidationFailed, Forbidden):
                    self.stats["refused_purchase_lines"] = self.stats.get("refused_purchase_lines", 0) + 1
                    continue
                it["stock"] += packs * it["pack"]
                self.stats["purchase_lines"] += 1
                added += 1
            if added:
                from loadgen import approve_with_reasons
                approve_with_reasons(p, ctx, inv)

        def one_sale(self, ctx, day, recent, att):
            # real items carry real rules (expiry, interactions, limits): a refused sale is counted, not fatal
            try:
                return super().one_sale(ctx, day, recent, att)
            except (Conflict, ValidationFailed, Forbidden):
                self.stats["refused_sales"] += 1
                for s in self.db.all("SELECT id FROM sales WHERE org_id=? AND status='draft' AND created_by=?",
                                     (ctx.org_id, ctx.user_id)):
                    try:
                        self.sales.discard(ctx, s["id"])
                    except (Conflict, ValidationFailed, Forbidden):
                        pass

        def snapshot_to(self, dest: Path) -> dict:
            dest.mkdir(parents=True)
            self.db.backup_to(dest / "pharmacy.db")
            snap = sqlite3.connect(dest / "pharmacy.db")
            loadgen.drop_gen_indexes(snap)
            snap.close()                                   # no VACUUM: it would need the database size again in disk
            if self.upload_dir.exists():
                shutil.copytree(self.upload_dir, dest / "uploads")
            return dict(self.stats)

    return RealGenerator(a, work)


# ------------------------------------------------------------------------------------------ measuring
def long_scenarios(f: dict, start: date) -> list[tuple[str, str]]:
    day = date.fromisoformat(f["day"])
    d, s0 = day.isoformat(), start.isoformat()
    w0 = (day - timedelta(days=6)).isoformat()
    m0 = day.replace(day=1).isoformat()
    out = [
        ("invoice list (week)", f"/api/sales?from={w0}&to={d}"),
        ("invoices report (all years)", f"/api/reports/invoices?from={s0}&to={d}"),
        ("sales report (all years)", f"/api/reports/sales?from={s0}&to={d}"),
        ("profit (year)", f"/api/reports/profit?from={(day - timedelta(days=365)).isoformat()}&to={d}"),
        ("profit (all years)", f"/api/reports/profit?from={s0}&to={d}"),
        ("item movements (all years, top seller)", f"/api/items/{f['top_item']}/movements?from={s0}&to={d}"),
        ("supplier statement (all years)", f"/api/suppliers/{f['supplier']}/statement?from={s0}&to={d}"),
        ("audit log (month)", f"/api/audit?from={m0}&to={d}"),
        ("returns report (all years)", f"/api/reports/owner/returns?from={s0}&to={d}"),
        ("staff report (all years)", f"/api/reports/owner/staff?from={s0}&to={d}"),
    ]
    # the rest of the owner's reports, each over the month and over everything (#253: every report, short and long)
    for name, path in (("price changes", "/api/reports/owner/prices"), ("unit changes", "/api/reports/owner/units"),
                       ("customer sales", "/api/reports/owner/customers"), ("shift closings", "/api/reports/owner/shifts"),
                       ("discounts", "/api/reports/owner/discounts"), ("unsold items", "/api/reports/owner/unsold"),
                       ("transfers", "/api/reports/transfers"), ("losses", "/api/reports/losses"),
                       ("item warnings", "/api/reports/item-warnings"), ("expenses", "/api/expenses")):
        out.append((f"{name} (month)", f"{path}?from={m0}&to={d}"))
        out.append((f"{name} (all years)", f"{path}?from={s0}&to={d}"))
    out += [("order suggestions", "/api/procurement/suggestions"), ("stock health", "/api/procurement/health"),
            ("notifications", "/api/notifications"), ("stock alert banner", "/api/stock/alert")]
    return out


def during_report(c, f: dict, path: str, method: str = "GET", body=None) -> dict:
    """The cashier keeps scanning while the owner opens the longest report (nothing may block the cashier)."""
    code, s, _, _ = c.req("POST", "/api/sales", {"customer_id": None})
    if code != 201:
        return {"error": f"create sale HTTP {code}"}
    sid = s["id"]
    lat: list[float] = []
    done = threading.Event()
    res: dict = {}

    def run():
        t = time.perf_counter()
        code, _, _, _ = c.req(method, path, body, timeout=1800)
        res.update(busy_s=round(time.perf_counter() - t, 1), status=code)
        done.set()

    th = threading.Thread(target=run)
    th.start()
    items = [x["id"] for x in f["stocked"]]
    k = 0
    while not done.is_set() and k < 4000:
        code, _, t, _ = c.req("POST", f"/api/sales/{sid}/lines", {"item_id": items[k % len(items)], "units": 1})
        lat.append(t)
        k += 1
        if code != 200:
            break
        time.sleep(0.1)
    th.join()
    c.req("POST", f"/api/sales/{sid}/discard", {})
    if lat:
        res.update(add_line_median_ms=round(statistics.median(lat), 1), add_line_max_ms=round(max(lat), 1), scans=len(lat))
    return res


PURCHASE_LINES = 20


def purchase_flow(c, f: dict) -> dict:
    """A purchase invoice typed by hand (PURCHASE_LINES lines) and approved while the cashier keeps scanning:
    receiving goods happens during opening hours, and it writes stock, cost and the supplier's account at once."""
    day = date.fromisoformat(f["day"])
    code, inv, _, _ = c.req("POST", "/api/purchases", {"supplier_id": f["supplier"], "invoice_date": day.isoformat(),
                                                       "invoice_no": f"PERF-{int(time.time())}"})
    if code != 201:
        return {"error": f"create HTTP {code}"}
    pid, ver = inv["invoice"]["id"], inv["invoice"]["version"]
    lat = []
    for k in range(PURCHASE_LINES):
        it = f["stocked"][k % len(f["stocked"])]
        price = it.get("public_price") or 1000
        net = max(1, price * 80 // 100)
        code, inv, t, _ = c.req("POST", f"/api/purchases/{pid}/lines", {"version": ver, "line": {
            "item_id": it["id"], "qty": 2, "bonus_qty": 0, "unit_net_price": net, "public_price": price, "line_total": 2 * net,
            "batch_no": f"PERF{k}", "expiry_date": (day + timedelta(days=500 + k)).isoformat()}})
        lat.append(t)
        if code != 200:
            return {"error": f"line {k + 1} HTTP {code}"}
        ver = inv["invoice"]["version"]
    out = {"add line": {"median_ms": round(statistics.median(lat), 1), "max_ms": round(max(lat), 1)}}
    for i in inv.get("issues", []):        # update 2 (U2-22): the near-expiry guards need the owner's written reason
        if i["code"] in ("short_expiry", "slow_before_expiry") and not i["acknowledged"]:
            code, inv, _, _ = c.req("POST", f"/api/purchases/{pid}/acknowledge", {
                "version": ver, "code": i["code"], "line_id": i["line_id"], "reason": "بيانات تجربة: كمية متفق عليها"})
            if code != 200:
                return {"error": f"acknowledge HTTP {code}"}
            ver = inv["invoice"]["version"]
    b = during_report(c, f, f"/api/purchases/{pid}/approve", "POST", {"version": ver})
    out["approve"] = b
    return out


def measure(snap: Path, owner_username: str, start: date, repeat: int, rec: dict, report: Report,
            backup: bool = True) -> None:
    """bench.py's measurements on one snapshot, with the copy's owner; results go into rec as they come.
    backup=False leaves out the backup timings and the scan-during-backup probe (scale.py, when the disk is short)."""
    import bench
    from loadgen import OWNER_PW
    work = snap.parent
    data = snap
    f = bench.facts(data / "pharmacy.db")
    if not f["stocked"]:
        rec["error"] = "no item with more than 20 units in stock on the last day: cashier flow skipped"
    rec["counts"] = f["counts"]
    rec["sizes"] = {"db_bytes": (data / "pharmacy.db").stat().st_size}
    report.write()
    if backup:
        report.progress(f"{rec['label']}: backup, verify, restore")
        rec["backup"] = bench.backup_measure(data, work)
        report.write()
    report.progress(f"{rec['label']}: server and requests")

    class Measured:
        """The measured server and a signed-in client."""

        def __init__(self):
            self.srv = bench.Server(data, f["end_local"])
            self.c = bench.Client(self.srv.base)
            code, body, _, _ = self.c.login(owner_username, OWNER_PW)
            if code != 200:
                self.srv.stop()
                why = (body.get("error") or {}).get("code") if isinstance(body, dict) else None   # code only, no message
                raise SystemExit(f"benchmark login refused (HTTP {code}, {why})")

    m = Measured()
    rec["startup"] = {"health_s": round(m.srv.ready_s, 2)}
    try:
        m.c.req("POST", "/api/cash/open", {"opening_cash": 10000})
        rec["requests"] = {}
        short, long = bench.scenarios(f), long_scenarios(f, start)
        # A report that does not answer in time keeps a server thread busy after the client gave up, and whatever is
        # measured next is measured under that load. So the server is started again after such a report (run()), and
        # the order keeps the damage small anyway: the daily screens, the cashier and a purchase invoice first, then
        # the long reports, then the blocking probes, and last the reports known to need minutes on a large catalog.
        last = [x for x in long if x[0] in LAST]

        def run(scen):
            nonlocal m
            for name, path in scen:
                r = rec["requests"][name] = bench.timed(m.c, "GET", path, n=repeat, timeout=REQUEST_TIMEOUT)
                report.write()
                if not isinstance(r["status"], int):
                    # no answer: the report goes on inside the server after the client gave up, and the next
                    # request would be measured under it. A new server (the shift stays open in the database).
                    rec["restarts"] = rec.get("restarts", 0) + 1
                    m.srv.stop()
                    m = Measured()

        run(short)
        if f["stocked"]:
            cf = bench.cashier_flow(m.c, f, n=repeat)
            if "errors" in cf:
                cf["errors"] = len(cf["errors"])           # error texts can carry item names: count only
            rec["cashier"] = cf
            report.write()
            report.progress(f"{rec['label']}: a purchase invoice")
            rec["purchase"] = purchase_flow(m.c, f)
            report.write()
        report.progress(f"{rec['label']}: long reports")
        run([x for x in long if x not in last])
        if f["stocked"]:
            report.progress(f"{rec['label']}: scanning while the server is busy")
            if backup:
                rec["during_backup"] = bench.during_backup(m.c, f)
            rec["during_report"] = during_report(m.c, f, dict(long)["sales report (all years)"])
            report.write()
            # nothing may block the cashier: scan again while each of the slowest requests runs (a second or more)
            slowest = sorted(((r["median_ms"], n) for n, r in rec["requests"].items()
                              if r.get("status") == 200 and r["median_ms"] >= 1000 and n != "sales report (all years)"),
                             reverse=True)[:SLOWEST]
            rec["during_slow"] = {n: during_report(m.c, f, dict(short + long)[n]) for _, n in slowest}
            report.write()
        # the owner's stock check «مطابقة الأرصدة» reads every stock movement: once, it is a button, not a screen
        rec["requests"]["stock check (run now)"] = bench.timed(m.c, "POST", "/api/reports/stock-check", n=1)
        report.write()
        report.progress(f"{rec['label']}: the heaviest reports")
        run(last)
    finally:
        m.srv.stop()
    srv = bench.Server(data, f["end_local"])
    rec["startup"]["health_warm_s"] = round(srv.ready_s, 2)
    srv.stop()
    shutil.rmtree(work / "tmp", ignore_errors=True)          # the servers' temp folder (bench.Server)


def summary(snaps: list[dict]) -> list[str]:
    """Per snapshot: ALL PASS only when everything was measured and nothing failed."""
    out = []
    for s in snaps:
        slow, failed = [], []
        if s.get("error"):
            failed.append(s["error"])
        cashier = s.get("cashier")
        if cashier is None:
            failed.append("cashier flow not measured")
        else:
            if cashier.get("errors"):
                failed.append(f"cashier refused {cashier['errors']} steps")
            for name, r in cashier.items():
                if isinstance(r, dict) and r["max_ms"] >= CASHIER_MS:
                    slow.append(f"{name} {r['max_ms']} ms")
        if not s.get("requests"):
            failed.append("reports not measured")
        for name, r in (s.get("requests") or {}).items():
            lim = CASHIER_MS if name in CASHIER_GETS else REPORT_MS
            if r.get("status") != 200:
                failed.append(f"{name} HTTP {r.get('status')}")
            elif r["max_ms"] >= lim:
                slow.append(f"{name} {r['max_ms']} ms")
        no_backup = bool(s.get("indexes_sql"))        # scale.py's second pass repeats the requests only
        for key in ("during_backup", "during_report"):
            b = s.get(key)
            if b is None:
                if not (no_backup and key == "during_backup"):
                    failed.append(f"{key} not measured")
                continue
            if b.get("error") or b.get("status") != 200:
                failed.append(f"{key} {b.get('error') or 'HTTP ' + str(b.get('status'))}")
            if b.get("add_line_max_ms") is not None and b["add_line_max_ms"] >= CASHIER_MS:
                slow.append(f"{key} scan {b['add_line_max_ms']} ms")
        for name, b in (s.get("during_slow") or {}).items():
            if b.get("error") or b.get("status") != 200:
                failed.append(f"scan during {name}: {b.get('error') or 'HTTP ' + str(b.get('status'))}")
            elif b.get("add_line_max_ms") is not None and b["add_line_max_ms"] >= CASHIER_MS:
                slow.append(f"scan during {name} {b['add_line_max_ms']} ms")
        pu = s.get("purchase")
        if pu is not None:                            # (absent in results written before this was measured)
            ap = pu.get("approve") or {}
            if pu.get("error") or ap.get("error") or ap.get("status") != 200:
                failed.append(f"purchase invoice: {pu.get('error') or ap.get('error') or 'approve HTTP ' + str(ap.get('status'))}")
            else:
                if pu["add line"]["max_ms"] >= CASHIER_MS:
                    slow.append(f"purchase add line {pu['add line']['max_ms']} ms")
                if ap.get("busy_s", 0) * 1000 >= REPORT_MS:
                    slow.append(f"purchase approve {ap['busy_s']} s")
                if ap.get("add_line_max_ms") is not None and ap["add_line_max_ms"] >= CASHIER_MS:
                    slow.append(f"scan during purchase approve {ap['add_line_max_ms']} ms")
        bk = s.get("backup")
        if bk is None and no_backup:
            pass
        elif bk is None:
            failed.append("backup not measured")
        elif bk.get("verify_problems") or bk.get("quick_check_problems"):
            failed.append("backup check found problems")
        verdict = "INCOMPLETE" if failed else ("ALL PASS" if not slow else f"{len(slow)} SLOW")
        out.append(f"{s['label']}: {verdict}" + (": " + "; ".join(failed + slow) if failed or slow else ""))
    return out


# ------------------------------------------------------------------------------------------ main
def run_profile(a, pname: str, live_db: Path, days: list[int], stop_at, report: Report) -> bool:
    """One pharmacy size on its own fresh copy. Returns False when the night ran out (stop-at or disk)."""
    labels = {d: (f"{d} days" if a.days else f"{d / 365:g} year" + ("s" if d != 365 else "")) for d in days}
    lo, hi = (int(x) for x in a.per_day.split("-"))
    work_parent = Path(a.work) if a.work else Path(tempfile.gettempdir())
    work = Path(tempfile.mkdtemp(prefix="pharm-real-years-", dir=work_parent))
    uploads = live_db.parent / "uploads"
    up_bytes = sum(f.stat().st_size for f in uploads.rglob("*") if f.is_file()) if uploads.is_dir() else 0
    need = 3 * (live_db.stat().st_size + up_bytes + days[-1] * (lo + hi) // 2 * 10_000)   # ~10 KB a sale (PERFORMANCE.md)
    if free_bytes(work) < need:
        report.note(f"{pname}: free disk {free_bytes(work) / 1e9:.1f} GB, estimated need {need / 1e9:.1f} GB:"
                    " it stops when the disk is short")
    gen = None
    went_on = True
    try:
        report.progress(f"{pname}: copying the live database (read-only)")
        (work / "gen" / "data").mkdir(parents=True)
        copy_live(live_db, work / "gen" / "data" / "pharmacy.db")
        if (live_db.parent / "uploads").is_dir():         # registered attachments: a restore refuses an archive without them
            shutil.copytree(live_db.parent / "uploads", work / "gen" / "data" / "uploads")
        report.progress(f"{pname}: preparing the copy")
        gen = make_generator(a, work)
        t0 = time.perf_counter()
        gen.setup()
        import loadgen
        loadgen.install_fast_matching(gen.db, gen.owner.org_id)      # the catalog is fixed from here (same data, faster)
        gen.restock(gen.start, initial=True)
        report.data["base"].setdefault("catalog_items", gen.base_counts["catalog_items"])
        report.data["base"].setdefault("items_in_stock", gen.base_counts["items_in_stock"])
        report.data["base"][f"{pname}_setup_s"] = round(time.perf_counter() - t0, 1)
        report.write()
        recent: list = []
        t1 = time.perf_counter()
        d = 0
        for target in days:
            stopped = False
            while d < target:
                if stop_at and datetime.now() >= stop_at:
                    stopped = True
                    break
                if free_bytes(work) < 3 * (work / "gen" / "data" / "pharmacy.db").stat().st_size + 500_000_000:
                    report.note(f"{pname}: disk almost full after {d} days: stopped adding days")
                    stopped = True
                    break
                d += 1
                gen.run_day(gen.start + timedelta(days=d), recent)
                if d % 10 == 0:
                    el = time.perf_counter() - t1
                    rate = gen.stats["sales"] / el
                    left = (days[-1] - d) * (lo + hi) / 2 / rate / 3600
                    report.progress(f"{pname}: day {d} of {days[-1]}, {gen.stats['sales']} sales, {rate:.1f} sales a second,"
                                    f" about {left:.1f} h left for this size")
            label = f"{pname} - " + (labels[target] if not stopped else f"{d} days (stopped early)")
            rec = {"label": label, "profile": pname, "days": d, "gen_s": round(time.perf_counter() - t1),
                   "stats": dict(gen.stats)}
            report.data["snapshots"].append(rec)
            if d == 0:
                rec["error"] = "no day was added"
                went_on = False
                break
            snap = work / f"snap-{d}" / "data"
            gen.snapshot_to(snap)
            try:
                measure(snap, gen.owner_username, gen.start, a.repeat, rec, report)
            except Exception as e:                               # keep what was measured, go on with the next snapshot
                rec["error"] = safe_error(e)
            finally:
                shutil.rmtree(snap.parent, ignore_errors=True)
            report.data["summary"] = summary(report.data["snapshots"])
            report.write()
            if stopped:
                went_on = False
                break
    finally:
        if gen is not None:
            try:
                gen.db.close()
            except Exception:
                pass
        if not a.keep_work:
            shutil.rmtree(work, ignore_errors=True)
    return went_on


def main(argv=None):
    use_cairo_time()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-data", default="", help="the program's data folder (default: the installed program's)")
    ap.add_argument("--out-txt", required=True, help="results text file (timings only); a .json is written next to it")
    ap.add_argument("--work", default="", help="folder for the temporary copy (default: the system temp folder)")
    ap.add_argument("--profiles", default="small,medium", help=f"pharmacy sizes in order, from {', '.join(PROFILES)};"
                    " empty = use --per-day/--items/--customers/--suppliers as given")
    ap.add_argument("--years", default="1,3,5", help="snapshots, in years of activity")
    ap.add_argument("--days", default="", help="snapshots in days (overrides --years; smoke runs)")
    ap.add_argument("--stop-at", default="", help="local HH:MM: stop adding days at this time and measure what exists")
    ap.add_argument("--items", type=int, default=4000, help="real catalog items that get synthetic demand")
    ap.add_argument("--suppliers", type=int, default=12)
    ap.add_argument("--customers", type=int, default=300)
    ap.add_argument("--credit-limit", type=int, default=500000)
    ap.add_argument("--per-day", default="150-300", help="sales per day, min-max")
    ap.add_argument("--transfer-share", type=float, default=0.02)
    ap.add_argument("--attachment-kb", type=float, default=60)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--seed", type=int, default=253)
    ap.add_argument("--keep-work", action="store_true", help="do not delete the work folder (debugging)")
    a = ap.parse_args(argv)
    a.quiet = True

    from loadgen import security  # noqa: F401  (loadgen sets a fast password cost for its synthetic accounts)
    from app import config

    report = Report(Path(a.out_txt).resolve())
    live = (Path(a.live_data) if a.live_data else config.load().data_dir).resolve()
    live_db = live / "pharmacy.db"
    if not live_db.is_file():
        report.data["status"] = "stopped"
        report.note("the program's database was not found")
        return 2
    names = [p.strip() for p in a.profiles.split(",") if p.strip()]
    bad = [p for p in names if p not in PROFILES]
    if bad:
        raise SystemExit(f"unknown size {bad}; choose from {list(PROFILES)}")
    days = [int(x) for x in a.days.split(",") if x] if a.days else [int(round(float(y) * 365)) for y in a.years.split(",") if y]
    days = sorted(set(days))
    stop_at = None
    if a.stop_at:
        hh, mm = (int(x) for x in a.stop_at.split(":"))
        now = datetime.now()
        stop_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if stop_at <= now:
            stop_at += timedelta(days=1)
    report.data["plan"] = {"sizes": {p: PROFILES[p] for p in names} if names else {"custom": {"per_day": a.per_day, "items": a.items}},
                           "snapshot_days": days, "stop_at": stop_at.isoformat(timespec="minutes") if stop_at else None,
                           "cpus": os.cpu_count(), "python": sys.version.split()[0]}
    report.data["base"] = {"db_mb": round(live_db.stat().st_size / 1e6, 1)}
    keep_awake()
    try:
        for pname in names or ["custom"]:
            if pname != "custom":
                for k, v in PROFILES[pname].items():
                    setattr(a, k, v)
            if not run_profile(a, pname, live_db, days, stop_at, report):
                break
        report.data["status"] = "finished"
    except BaseException as e:
        report.data["status"] = "failed"
        report.note(safe_error(e))
        raise
    finally:
        report.progress(report.data["progress"] + ("" if a.keep_work else "  (work folders deleted)"))
    print("\n".join(report.data.get("summary") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
