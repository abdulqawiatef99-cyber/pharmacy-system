#!/usr/bin/env python3
"""Measure one synthetic snapshot made by loadgen.py (PERFORMANCE.md). Never point it at real data.

  python scripts/perf/bench.py --data /tmp/perf/y1/data --label y1 --json /tmp/perf/y1.json

It copies the snapshot to a temporary folder first (the snapshot is never changed) and measures:
  * sizes: database file, attachments, row counts;
  * backup through the program's own path (backup.create + quick_check, what the hourly automatic
    backup runs), the backup file size, verify (backup.verify_archive) and restore (backup.restore);
  * `manage.py serve` start-up until /api/health answers, and the server's peak memory (VmHWM);
  * the cashier hot paths and the owner's daily lists and reports through HTTP (median and max of N);
  * the cashier's add-line latency while the automatic backup runs in the server;
  * with --explain: every SQL statement slower than --slow-ms in an in-process run of the same
    requests, with EXPLAIN QUERY PLAN.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# PHARM_PROGRAM_DIR: run these scripts from a folder outside the installed program (real_years.py on the PC)
ROOT = Path(os.environ.get("PHARM_PROGRAM_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT))
if os.name != "nt":                      # Windows C runtime misreads an IANA name in TZ; it already runs on local time
    os.environ.setdefault("TZ", "Africa/Cairo")
if hasattr(time, "tzset"):
    time.tzset()

OWNER_PW = "Perf-Owner-2026"         # the synthetic owner made by loadgen.py
H = {"X-Requested-With": "pharm", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


class Client:
    def __init__(self, base: str):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                              urllib.request.ProxyHandler({}))
        self.creds: dict | None = None

    def login(self, username: str, password: str):
        """Sign in and remember how: a snapshot that ends more than 30 days before the real today loses its
        sessions to the daily clean-up (it compares with SQLite's own clock, which serve_at.py does not move),
        so a request answered 401 signs in again and is asked once more."""
        self.creds = None
        out = self.req("POST", "/api/auth/login", {"username": username, "password": password})
        if out[0] == 200:
            self.creds = {"username": username, "password": password}
        return out

    def req(self, method: str, path: str, body=None, timeout=600):
        out = self._req(method, path, body, timeout)
        if out[0] == 401 and self.creds:
            creds, self.creds = self.creds, None
            if self._req("POST", "/api/auth/login", creds, 60)[0] == 200:
                self.creds = creds
                out = self._req(method, path, body, timeout)
        return out

    def _req(self, method: str, path: str, body=None, timeout=600):
        data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=H)
        t = time.perf_counter()
        try:
            with self.op.open(r, timeout=timeout) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        ms = (time.perf_counter() - t) * 1000
        try:
            out = json.loads(raw) if raw else None
        except ValueError:
            out = None
        return code, out, ms, len(raw)


# ------------------------------------------------------------------------------------------ fixtures
def facts(db_path: Path) -> dict:
    """Ids and dates the scenarios need, read from the copy (read-only)."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    last = c.execute("SELECT max(completed_at) m FROM sales WHERE status='completed'").fetchone()["m"]
    last_local = datetime.strptime(last, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=__import__("datetime").timezone.utc).astimezone()
    day = last_local.date()
    top = c.execute("SELECT l.item_id, count(*) n FROM sale_lines l JOIN sales s ON s.id=l.sale_id"
                    " WHERE s.completed_at >= ? GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
                    ((day - timedelta(days=30)).isoformat(),)).fetchall()
    # items the cashier can really sell on the snapshot's day: unexpired stock only, as Sales._sellable counts it
    stocked = c.execute("SELECT i.id, i.name_en, min(bc.barcode) barcode, i.public_price FROM items i"
                        " JOIN item_barcodes bc ON bc.item_id=i.id"
                        " JOIN stock_balances sb ON sb.item_id=i.id AND sb.qty>0"
                        " LEFT JOIN batches bt ON bt.id=nullif(sb.batch_key,'')"
                        " WHERE bt.expiry_date IS NULL OR bt.expiry_date > ?"
                        " GROUP BY i.id HAVING sum(sb.qty) > 20 ORDER BY i.id LIMIT 3", (day.isoformat(),)).fetchall()
    sup = c.execute("SELECT supplier_id, count(*) n FROM supplier_ledger GROUP BY 1 ORDER BY 2 DESC LIMIT 1").fetchone()
    cust = c.execute("SELECT customer_id, count(*) n FROM customer_ledger GROUP BY 1 ORDER BY 2 DESC LIMIT 1").fetchone()
    shift = c.execute("SELECT id FROM cash_shifts WHERE status='closed' ORDER BY opened_at DESC LIMIT 1").fetchone()
    counts = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
              ("items", "batches", "sales", "sale_lines", "sale_returns", "purchase_invoices", "purchase_invoice_lines",
               "stock_movements", "audit_log", "safety_events", "customer_ledger", "supplier_ledger", "cash_shifts",
               "cash_movements", "source_files", "notifications")}
    try:
        tables = {r[0]: r[1] for r in c.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC LIMIT 12")}
    except sqlite3.Error:
        tables = {}
    c.close()
    return {"day": day.isoformat(), "end_local": last_local.replace(tzinfo=None).isoformat(timespec="minutes"),
            "top_item": top[0]["item_id"], "stocked": [dict(r) for r in stocked],
            "supplier": sup["supplier_id"], "customer": cust["customer_id"] if cust else None,
            "shift": shift["id"], "counts": counts, "largest_tables": tables}


def scenarios(f: dict) -> list[tuple[str, str]]:
    day = date.fromisoformat(f["day"])
    d = day.isoformat()
    m0 = day.replace(day=1).isoformat()
    y0 = (day - timedelta(days=365)).isoformat()
    name = f["stocked"][0]["name_en"].split()[0][:4].lower()
    out = [
        ("search name (4 letters)", f"/api/items?q={name}&limit=20"),
        ("search barcode", f"/api/items?q={f['stocked'][0]['barcode']}&limit=20"),
        ("POS quick buttons", "/api/pos/quick"),
        ("home screen", "/api/home"),
        ("invoice list (day)", f"/api/sales?day={d}"),
        ("invoices report (month)", f"/api/reports/invoices?from={m0}&to={d}"),
        ("purchases list", "/api/purchases"),
        ("day report", f"/api/sales/summary?day={d}"),
        ("sales report (day)", f"/api/reports/sales?from={d}&to={d}"),
        ("sales report (month)", f"/api/reports/sales?from={m0}&to={d}"),
        ("sales report (year)", f"/api/reports/sales?from={y0}&to={d}"),
        ("profit (month)", f"/api/reports/profit?from={m0}&to={d}"),
        ("stock report", "/api/reports/stock"),
        ("item card (top seller)", f"/api/items/{f['top_item']}/card"),
        ("item movements (1 year, top seller)", f"/api/items/{f['top_item']}/movements?from={y0}&to={d}"),
        ("supplier statement (1 year)", f"/api/suppliers/{f['supplier']}/statement?from={y0}&to={d}"),
        ("suppliers list", "/api/suppliers"),
        ("shift list", "/api/cash/shifts"),
        ("shift report", f"/api/cash/shifts/{f['shift']}"),
        ("audit log (day)", f"/api/audit?from={d}&to={d}"),
        ("returns report (month)", f"/api/reports/owner/returns?from={m0}&to={d}"),
        ("staff report (month)", f"/api/reports/owner/staff?from={m0}&to={d}"),
    ]
    if f["customer"]:
        out.insert(16, ("customer statement", f"/api/customers/{f['customer']}"))
    return out


# ------------------------------------------------------------------------------------------ server
class Server:
    def __init__(self, data: Path, at: str, env_extra: dict | None = None):
        self.port = free_port()
        # every path explicitly: an inherited PHARM_DB_PATH / PHARM_UPLOAD_DIR (the Windows release validation sets
        # them) would make the server open another database than the snapshot
        env = {**os.environ, "PHARM_DATA_DIR": str(data), "PHARM_DB_PATH": str(data / "pharmacy.db"),
               "PHARM_UPLOAD_DIR": str(data / "uploads"), "PHARM_BACKUP_DIR": str(data.parent / "backups"),
               "PYTHONUNBUFFERED": "1", **(env_extra or {})}
        self.t0 = time.perf_counter()
        self.p = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "serve_at.py"), "--at", at, "--port", str(self.port)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.log: list[str] = []
        threading.Thread(target=self._drain, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.ready_s = self._wait()

    def _drain(self):
        for line in self.p.stdout:
            self.log.append(line.rstrip())

    def _wait(self) -> float:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.perf_counter() + 300
        while time.perf_counter() < deadline:
            if self.p.poll() is not None:
                raise RuntimeError("server exited: " + "\n".join(self.log[-20:]))
            try:
                with op.open(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        return time.perf_counter() - self.t0
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server did not start")

    def mem(self) -> dict:
        out = {}
        try:
            for line in Path(f"/proc/{self.p.pid}/status").read_text().splitlines():
                k, _, v = line.partition(":")
                if k in ("VmHWM", "VmRSS"):
                    out[k] = int(v.split()[0]) // 1024        # MB
        except OSError:
            pass
        return out

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(30)
        except subprocess.TimeoutExpired:
            self.p.kill()


def timed(client: Client, method: str, path: str, body=None, n: int = 5, timeout: float = 600) -> dict:
    ms, size, code = [], 0, None
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            code, out, t, size = client.req(method, path, body, timeout)
        except OSError as e:                       # no answer within the client's timeout: recorded, not fatal
            code, t = type(e).__name__, (time.perf_counter() - t0) * 1000
        ms.append(t)
        if t >= 30_000 or not isinstance(code, int):
            break                                  # half a minute or more: once is enough
    return {"median_ms": round(statistics.median(ms), 1), "max_ms": round(max(ms), 1), "status": code, "bytes": size}


def cashier_flow(c: Client, f: dict, n: int = 5) -> dict:
    """create sale, add 3 lines, check, complete (cash), then return one unit; n times."""
    res = {k: [] for k in ("create sale", "add line", "safety check", "complete sale", "return one unit", "receipt page")}
    items = [s["id"] for s in f["stocked"]]
    errors = []
    for k in range(n):
        code, s, t, _ = c.req("POST", "/api/sales", {"customer_id": None})
        res["create sale"].append(t)
        if code != 201:
            errors.append(("create", code, s))
            continue
        sid, draft = s["id"], None
        for it in items:
            code, body, t, _ = c.req("POST", f"/api/sales/{sid}/lines", {"item_id": it, "units": 1})
            res["add line"].append(t)
            if code == 200:
                draft = body                                # the sale as of its last line that went in
            else:
                errors.append(("line", code, body))
        if draft is None:                                   # nothing sellable: skip this sale, keep the others
            continue
        code, chk, t, _ = c.req("POST", f"/api/sales/{sid}/check", {})
        res["safety check"].append(t)
        findings = (chk or {}).get("findings", [])
        code, done, t, _ = c.req("POST", f"/api/sales/{sid}/complete", {
            "payments": [{"method": "cash", "amount": draft["total"]}], "version": draft["version"],
            "request_id": f"bench-{sid}", "safety_decisions": {str(i): "مراجعة" for i in range(len(findings))}})
        res["complete sale"].append(t)
        if code != 200:
            errors.append(("complete", code, done))
            continue
        code, _, t, _ = c.req("GET", f"/receipt/{sid}")
        res["receipt page"].append(t)
        code, r, t, _ = c.req("POST", f"/api/sales/{sid}/return", {"lines": {done["lines"][0]["id"]: 1},
                                                                    "refund_method": "cash", "reason": "قياس الأداء"})
        res["return one unit"].append(t)
        if code != 200:
            errors.append(("return", code, r))
    out = {k: {"median_ms": round(statistics.median(v), 1), "max_ms": round(max(v), 1)} for k, v in res.items() if v}
    if errors:
        out["errors"] = [str(e)[:300] for e in errors[:3]]
    return out


def during_backup(c: Client, f: dict) -> dict:
    """The cashier keeps scanning while a backup runs in the server (what the hourly backup does)."""
    code, s, _, _ = c.req("POST", "/api/sales", {"customer_id": None})
    sid = s["id"]
    lat: list[float] = []
    done = threading.Event()
    result = {}

    def run_backup():
        t = time.perf_counter()
        code, out, _, _ = c.req("POST", "/api/backup/run", {}, timeout=1800)
        result.update(backup_s=round(time.perf_counter() - t, 1), status=code,
                      ok=(out or {}).get("last_ok") if isinstance(out, dict) else None)
        done.set()

    th = threading.Thread(target=run_backup)
    th.start()
    time.sleep(0.2)
    items = [x["id"] for x in f["stocked"]]
    k = 0
    while not done.is_set() and k < 2000:
        code, _, t, _ = c.req("POST", f"/api/sales/{sid}/lines", {"item_id": items[k % len(items)], "units": 1})
        lat.append(t)
        k += 1
        if code != 200:
            break
        time.sleep(0.25)                  # a scan every quarter second
    th.join()
    c.req("POST", f"/api/sales/{sid}/discard", {})
    if lat:
        result.update(add_line_median_ms=round(statistics.median(lat), 1), add_line_max_ms=round(max(lat), 1),
                      add_line_p95_ms=round(sorted(lat)[int(len(lat) * 0.95) - 1 if len(lat) > 1 else 0], 1),
                      scans=len(lat))
    return result


# ------------------------------------------------------------------------------------------ backup
def backup_measure(data: Path, work: Path) -> dict:
    from app.core import backup
    from app.db import Database
    out = {}
    db = Database(data / "pharmacy.db")
    dest = work / "bk"
    t = time.perf_counter()
    arc = backup.create(db, data / "uploads", dest, tag="auto")
    out["create_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    problems = backup.quick_check(arc)
    out["quick_check_s"] = round(time.perf_counter() - t, 2)
    db.close()
    out["backup_bytes"] = arc.stat().st_size
    out["quick_check_problems"] = problems
    t = time.perf_counter()
    info = backup.verify_archive(arc)
    out["verify_s"] = round(time.perf_counter() - t, 2)
    out["verify_problems"] = info["problems"]
    tgt = work / "restored"
    t = time.perf_counter()
    backup.restore(arc, tgt / "pharmacy.db", tgt / "uploads", work / "pre")
    out["restore_s"] = round(time.perf_counter() - t, 2)
    # where the backup time goes (the same steps backup.create runs)
    from app.core import audit
    from app.inventory import service as inventory
    db = Database(tgt / "pharmacy.db")
    t = time.perf_counter()
    db.one("PRAGMA integrity_check")
    out["step_integrity_check_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    for o in db.all("SELECT id FROM organizations"):
        inventory.verify_balances(db, o["id"])
    out["step_verify_balances_s"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    audit.verify_chain(db)
    out["step_audit_chain_s"] = round(time.perf_counter() - t, 2)
    db.close()
    import lzma
    t = time.perf_counter()
    with open(tgt / "pharmacy.db", "rb") as src, lzma.open(work / "x.xz", "wb", preset=backup.XZ_PRESET) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    out["step_xz_s"] = round(time.perf_counter() - t, 2)
    shutil.rmtree(tgt, ignore_errors=True)
    shutil.rmtree(work / "pre", ignore_errors=True)
    (work / "x.xz").unlink(missing_ok=True)
    return out


# ------------------------------------------------------------------------------------------ explain
def explain(data: Path, f: dict, slow_ms: float) -> list[dict]:
    """Run the same requests in-process with a timing Database; EXPLAIN QUERY PLAN for slow SQL."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from serve_at import _RunningClock
    from simclock import install_python
    install_python(_RunningClock(datetime.fromisoformat(f["end_local"]).astimezone()))
    from app import config
    from app.db import Database
    import manage

    seen: dict[str, dict] = {}

    class Timed(Database):
        def _q(self, kind, sql, params):
            t = time.perf_counter()
            cur = self.conn.execute(sql, params)
            rows = cur.fetchall() if kind == "all" else cur.fetchone() if kind == "one" else cur
            ms = (time.perf_counter() - t) * 1000
            k = " ".join(sql.split())
            e = seen.setdefault(k, {"sql": k, "params": params, "max_ms": 0, "calls": 0, "total_ms": 0})
            e["calls"] += 1
            e["total_ms"] += ms
            if ms > e["max_ms"]:
                e["max_ms"], e["params"] = round(ms, 1), params
            return rows

        def one(self, sql, params=()):
            return self._q("one", sql, params)

        def all(self, sql, params=()):
            return self._q("all", sql, params)

    os.environ["PHARM_DATA_DIR"] = str(data)
    s = config.load()
    db = manage._db(s)
    db.__class__ = Timed
    import uvicorn
    from app.api.app import create_app
    app = create_app(db, s, auto_backup=False)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    while not srv.started:
        time.sleep(0.05)
    c = Client(f"http://127.0.0.1:{port}")
    c.login("owner", OWNER_PW)
    c.req("POST", "/api/cash/open", {"opening_cash": 10000})
    for name, path in scenarios(f):
        c.req("GET", path)
    cashier_flow(c, f, n=1)
    srv.should_exit = True
    th.join(10)
    out = []
    big = ("sales", "sale_lines", "sale_allocations", "sale_payments", "sale_returns", "stock_movements", "audit_log",
           "safety_events", "cash_movements", "customer_ledger", "supplier_ledger", "notifications", "batches",
           "purchase_invoice_lines", "purchase_invoices", "item_price_history", "sessions")
    for e in sorted(seen.values(), key=lambda e: -e["total_ms"]):
        try:
            plan = [r[3] for r in db.conn.execute("EXPLAIN QUERY PLAN " + e["sql"], e["params"]).fetchall()]
        except sqlite3.Error as err:
            plan = [f"(no plan: {err})"]
        scans = [p for p in plan if p.startswith("SCAN ") and p.split()[1] in big]
        if e["max_ms"] >= slow_ms or e["total_ms"] >= 10 * slow_ms or scans:
            out.append({"sql": e["sql"][:600], "max_ms": e["max_ms"], "calls": e["calls"],
                        "total_ms": round(e["total_ms"], 1), "full_scans": scans, "plan": plan})
    db.close()
    return out


# ------------------------------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="a loadgen snapshot data folder (copied, never changed)")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--skip-backup", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--slow-ms", type=float, default=50)
    a = ap.parse_args(argv)
    src = Path(a.data).resolve()
    work = Path(tempfile.mkdtemp(prefix="pharm-bench-"))
    data = work / "data"
    shutil.copytree(src, data)
    res: dict = {"label": a.label, "machine": {"cpus": os.cpu_count(), "python": sys.version.split()[0]},
                 "measured_at": datetime.now().isoformat(timespec="seconds")}
    try:
        f = facts(data / "pharmacy.db")
        res["facts"] = {k: f[k] for k in ("day", "counts", "largest_tables")}
        res["sizes"] = {"db_bytes": (data / "pharmacy.db").stat().st_size, "uploads_bytes": dir_bytes(data / "uploads"),
                        "uploads_files": sum(1 for p in (data / "uploads").rglob("*") if p.is_file()) if (data / "uploads").exists() else 0}
        if not a.skip_backup:
            print("backup ...", flush=True)
            res["backup"] = backup_measure(data, work)
        print("server ...", flush=True)
        srv = Server(data, f["end_local"])
        res["startup"] = {"health_s": round(srv.ready_s, 2), "mem_after_start_mb": srv.mem()}
        try:
            c = Client(srv.base)
            code, _, _, _ = c.login("owner", OWNER_PW)
            assert code == 200, code
            c.req("POST", "/api/cash/open", {"opening_cash": 10000})
            res["requests"] = {}
            for name, path in scenarios(f):
                res["requests"][name] = timed(c, "GET", path, n=a.repeat)
                print(f"  {name}: {res['requests'][name]}", flush=True)
            res["cashier"] = cashier_flow(c, f, n=a.repeat)
            print("  cashier:", res["cashier"], flush=True)
            if not a.skip_backup:
                res["during_backup"] = during_backup(c, f)
                print("  during backup:", res["during_backup"], flush=True)
            res["memory_mb"] = srv.mem()
        finally:
            srv.stop()
        # a second start, the files now warm in the OS cache (what the owner sees on the second start of the day)
        srv = Server(data, f["end_local"])
        res["startup"]["health_warm_s"] = round(srv.ready_s, 2)
        srv.stop()
        if a.explain:
            print("explain ...", flush=True)
            res["slow_sql"] = explain(data, f, a.slow_ms)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k not in ("requests", "slow_sql")}, ensure_ascii=False, indent=1, default=str))
    return res


if __name__ == "__main__":
    main()
