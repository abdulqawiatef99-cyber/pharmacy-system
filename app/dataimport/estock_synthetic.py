"""A synthetic E-Stock export at the pharmacy's real size, and a timed run of the move over it.

Every value is invented (no real pharmacy data, ever). The export has estock-db-read.ps1's layout and the
schema the move reads, at the sizes measured on the pharmacy PC (19/11): about 30k items, 14k stock batches,
27k sale headers with 52k lines, 3k purchases with 24k lines, 15k customer statement rows, 27k money journal
rows, 1.4k customers and 62 suppliers. The data is consistent (balances equal their statements, stock is in
whole units) so a run over it must end RECONCILIATION: OK.

    python -m app.dataimport.estock_synthetic --bench            # full size: read, dry run, apply, timed
    python -m app.dataimport.estock_synthetic --bench --scale 0.1
    python -m app.dataimport.estock_synthetic --write FOLDER     # only write the export

The bench runs the same manage.py commands as estock-db-import.bat, each in its own process, on a new empty
pharmacy in a temp folder, and prints seconds and peak memory per step. Run it on the pharmacy PC before the
launch night to know the real times there.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

FULL = {"items": 30000, "batches": 14000, "sales": 27000, "sale_lines": 52000, "purchases": 3000,
        "purchase_lines": 24000, "customer_rows": 15000, "journal_rows": 27000, "customers": 1400, "suppliers": 62}
BRANCH = "3"
PACKS = (1, 1, 1, 2, 3, 3, 5, 10, 14, 20, 30)
_AR = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
_EN = "abcdefghijklmnopqrstuvwxyz"


def sizes(scale: float = 1.0) -> dict[str, int]:
    return {k: max(1, int(round(v * scale))) for k, v in FULL.items()}


def _word(rnd: random.Random, letters: str, n: int) -> str:
    return "".join(rnd.choice(letters) for _ in range(n))


def _money(rnd: random.Random, lo: float, hi: float) -> str:
    return f"{rnd.uniform(lo, hi):.4f}"


def _at(base: datetime, rnd: random.Random, days: int) -> str:
    return (base - timedelta(days=rnd.randint(0, days), seconds=rnd.randint(0, 86399))).strftime("%Y-%m-%dT%H:%M:%S.000")


def tables(scale: float = 1.0, seed: int = 1, wide: bool = True) -> dict[str, list[dict]]:
    """The export's tables as rows of text, like estock-db-read.ps1 writes them."""
    n, rnd = sizes(scale), random.Random(seed)
    now = datetime(2026, 11, 18, 22, 0, 0)
    t: dict[str, list[dict]] = {}
    t["co_inf"] = [{"branch_id": BRANCH, "co_name": "صيدلية تجريبية"}]
    t["Companys"] = [{"company_id": str(i), "company_code": str(i), "co_name_ar": f"شركة {_word(rnd, _AR, 5)}",
                      "co_name_en": f"Co {_word(rnd, _EN, 6)}"} for i in range(1, 301)]
    t["Stores"] = [{"store_id": "1", "store_code": "1", "store_name_ar": "الصيدليه", "store_name_en": "", "active": "1"},
                   {"store_id": "2", "store_code": "2", "store_name_ar": "مستشفيات تجربة", "store_name_en": "", "active": "1"}]
    t["Product_units"] = [{"unit_id": "1", "unit_name_ar": "علبة"}, {"unit_id": "2", "unit_name_ar": "شريط"},
                          {"unit_id": "3", "unit_name_ar": "قرص"}]
    t["Product_groups"] = [{"group_id": str(i), "group_name_ar": f"مجموعة {i}", "group_name_en": f"Group {i}"}
                           for i in range(1, 41)]
    items = []
    for pid in range(1, n["items"] + 1):
        pack = rnd.choice(PACKS)
        sell = rnd.uniform(3, 900)
        r = {"product_id": str(pid), "product_code": str(10000 + pid),
             "product_name_en": f"{_word(rnd, _EN, 7).title()} {rnd.choice((50, 100, 250, 500, 1000))}mg Tab",
             "product_name_ar": f"{_word(rnd, _AR, 6)} أقراص", "product_scientific_name": _word(rnd, _EN, 9),
             "company_id": str(rnd.randint(1, 300)), "group_id": str(rnd.randint(1, 40)),
             "product_drug": rnd.choice("1110"), "deleted": "0", "active": "1",
             "sell_price": f"{sell:.4f}", "buy_price": f"{sell * rnd.uniform(0.7, 0.92):.4f}",
             "product_disc1": "0", "product_disc2": "0", "product_unit1": "1",
             "product_unit2": "2" if pack > 1 else "", "product_unit1_2": f"{pack}.0000" if pack > 1 else "",
             "product_unit3": "", "product_unit1_3": "",
             "product_int_code": f"622{pid:010d}", "product_has_expire": "1"}
        items.append((pid, pack, sell))
        t.setdefault("Products", []).append(r)
    # stock: one row per E-Stock batch, whole units of the item's pack (a fraction of a pack is k/pack)
    t["Product_Amount"] = []
    for pa in range(1, n["batches"] + 1):
        pid, pack, sell = items[rnd.randrange(len(items))]
        units = rnd.randint(1, 60)
        store = "2" if rnd.random() < 0.03 else "1"
        t["Product_Amount"].append({
            "branch_id": BRANCH, "counter_id": str(pa), "product_id": str(pid), "store_id": store, "vendor_id": "1",
            "amount": str(units // pack) if units % pack == 0 else repr(units / pack),
            "buy_price": f"{sell * 0.8:.4f}", "sell_price": f"{sell:.4f}", "tax_price": "0",
            "exp_date": (now + timedelta(days=rnd.randint(30, 1100))).strftime("%Y-%m-%dT00:00:00"),
            "insert_uid": "1", "insert_date": _at(now, rnd, 700), "update_uid": "1",
            "update_date": _at(now, rnd, 5), "pa_id": str(pa)})
    t["Branches_Product_Amount"] = [dict(r) for r in t["Product_Amount"]]
    # customers and their statements: the balance is the statement's sum and its last running total
    t["Customer"], t["Gedo_customers"] = [], []
    per = max(1, n["customer_rows"] // n["customers"])
    gc = 0
    for cid in range(1, n["customers"] + 1):
        total = 0.0
        for _ in range(per if cid < n["customers"] else n["customer_rows"] - gc):
            gc += 1
            charge = rnd.random() < 0.6
            v = round(rnd.uniform(5, 800), 2)
            total = round(total + (v if charge else -v), 2)
            t["Gedo_customers"].append({"gc_id": str(gc), "customer_id": str(cid),
                                        "gc_for_me": f"{v:.4f}" if charge else "0", "gc_for_him": "0" if charge else f"{-v:.4f}",
                                        "total": f"{total:.4f}", "insert_date": _at(now, rnd, 600)})
        t["Customer"].append({"customer_id": str(cid), "customer_code": f"C{cid}",
                              "customer_name_ar": f"عميل {_word(rnd, _AR, 4)} {cid}", "customer_name_en": "",
                              "mobile": f"010{cid:08d}", "tel": "", "address": "",
                              "customer_current_money": f"{total:.4f}", "customer_max_money": "0",
                              "deleted": "0", "active": "1"})
    for r in t["Gedo_customers"]:          # a running total in time order, as E-Stock keeps it
        r["insert_date"] = (now - timedelta(minutes=n["customer_rows"] - int(r["gc_id"]))).strftime("%Y-%m-%dT%H:%M:%S.000")
    # suppliers: a statement of their purchases and payments
    t["Vendor"], t["Gedo_Vendors"] = [], []
    t["Purchase_header"], t["Purchase_details"] = [], []
    per_p = max(1, n["purchase_lines"] // n["purchases"])
    line = 0
    for pur in range(1, n["purchases"] + 1):
        vid = rnd.randint(1, n["suppliers"])
        tot = 0.0
        for _ in range(per_p if pur < n["purchases"] else n["purchase_lines"] - line):
            line += 1
            pid, pack, sell = items[rnd.randrange(len(items))]
            amt = rnd.randint(1, 20)
            buy = round(sell * 0.8, 4)
            tot += amt * buy
            t["Purchase_details"].append({"details_id": str(line), "purchase_id": str(pur), "product_id": str(pid),
                                          "amount": str(amt), "buy_price": f"{buy:.4f}"})
        t["Purchase_header"].append({"purchase_id": str(pur), "vendor_id": str(vid), "class": "1",
                                     "total_bill": f"{tot:.4f}", "total_after_back": f"{tot:.4f}",
                                     "bill_date": _at(now, rnd, 600), "insert_date": _at(now, rnd, 600)})
    vbal = {v: 0.0 for v in range(1, n["suppliers"] + 1)}
    gv = 0
    for h in t["Purchase_header"]:
        vid = int(h["vendor_id"])
        for amount in (float(h["total_bill"]), -float(h["total_bill"]) * rnd.choice((0, 0.5, 1))):
            if not amount:
                continue
            gv += 1
            vbal[vid] = round(vbal[vid] + amount, 2)
            t["Gedo_Vendors"].append({"gv_id": str(gv), "vendor_id": str(vid),
                                      "gv_for_him": f"{amount:.4f}" if amount > 0 else "0",
                                      "gv_for_me": f"{amount:.4f}" if amount < 0 else "0",
                                      "total": f"{vbal[vid]:.4f}",
                                      "insert_date": (now - timedelta(minutes=20000 - gv)).strftime("%Y-%m-%dT%H:%M:%S.000")})
    for v in range(1, n["suppliers"] + 1):
        t["Vendor"].append({"vendor_id": str(v), "vendor_code": f"V{v}", "vendor_name_ar": f"مورد {_word(rnd, _AR, 5)} {v}",
                            "mobile": f"011{v:08d}", "vendor_current_money": f"{vbal[v]:.4f}", "deleted": "0", "active": "1"})
    # sales: headers and this branch's lines; the paid parts add up to the net
    t["Sales_header"], t["Branches_sales_details"] = [], []
    per_s = max(1, n["sale_lines"] // n["sales"])
    extra = n["sale_lines"] - per_s * n["sales"]
    det = 0
    for sid in range(1, n["sales"] + 1):
        net = 0.0
        at = _at(now, rnd, 400)
        for _ in range(per_s + (1 if sid <= extra else 0)):
            det += 1
            pid, pack, sell = items[rnd.randrange(len(items))]
            amt = rnd.choice((1, 1, 1, 2, 0.5)) if pack > 1 else rnd.randint(1, 3)
            ts = round(amt * sell, 2)
            net += ts
            t["Branches_sales_details"].append({
                "branch_id": BRANCH, "details_id": str(det), "sales_id": str(sid), "product_id": str(pid),
                "amount": str(amt), "sale_unit_change": str(pack), "sale_unit": "1", "sell_price": f"{sell:.4f}",
                "buy_price": f"{sell * 0.8:.4f}", "total_sell": f"{ts:.4f}", "counter_id": "0",
                "exp_date": "2027-06-01T00:00:00", "insert_date": at})
        cust = str(rnd.randint(1, n["customers"])) if rnd.random() < 0.2 else "0"
        card = round(net * 0.3, 2) if rnd.random() < 0.1 else 0
        t["Sales_header"].append({"sales_id": str(sid), "store_id": "1", "customer_id": cust,
                                  "total_bill_net": f"{net:.4f}", "bill_cash": f"{net - card:.4f}",
                                  "network_money": f"{card:.4f}", "money_change": "0", "insert_date": at,
                                  "ticket_name": "", "ticket_id": "", "ticket_num": ""})
    t["Back_sales_header"], t["Back_Sales_details"] = [], []
    # the money journal (history only)
    t["Gedo_Financial"] = [{"gf_id": str(i), "depot_id": str(rnd.randint(1, 3)), "gf_for_me": _money(rnd, 0, 900),
                            "gf_for_him": "0", "insert_date": _at(now, rnd, 600)}
                           for i in range(1, n["journal_rows"] + 1)]
    t["Shortcoming"] = []
    t["Cash_disk_close"] = [{"cdc_id": "1", "cdc_curr_cash": "1000.0000", "cdc_act_cash": "1000.0000"}]
    if wide:
        _widen(t, rnd)
    return t


# E-Stock's tables are much wider than the columns the move reads (its database is ~120 MB). Extra columns of
# the usual kinds (money, flags, ids, dates, short text) bring the export, the memory and the archive to a
# realistic size; the move keeps them in the archive and otherwise ignores them.
WIDTH = {"Products": 75, "Product_Amount": 22, "Branches_Product_Amount": 22, "Sales_header": 55,
         "Branches_sales_details": 38, "Purchase_header": 45, "Purchase_details": 35, "Customer": 40,
         "Gedo_customers": 18, "Gedo_Vendors": 18, "Gedo_Financial": 20, "Vendor": 35}


def _widen(t: dict[str, list[dict]], rnd: random.Random) -> None:
    kinds = ("0.0000", "1", "0", "", "2026-01-01T00:00:00.000", "ملاحظة")
    for name, width in WIDTH.items():
        rows = t.get(name) or []
        have = len(rows[0]) if rows else 0
        extra = [(f"x_col{i}", kinds[i % len(kinds)]) for i in range(max(0, width - have))]
        for r in rows:
            for c, v in extra:
                r[c] = v if v != "0.0000" else f"{rnd.uniform(0, 99):.4f}"


def write(folder: Path, scale: float = 1.0, seed: int = 1, wide: bool = True) -> Path:
    """One read as estock-db-read.ps1 leaves it: folder/tables/... + manifest.csv + summary.txt (PASS)."""
    folder = Path(folder)
    root = folder / "tables"
    dbdir = root / "01_PC" / "01_stock"
    dbdir.mkdir(parents=True)
    man = []
    for i, (name, rows) in enumerate(tables(scale, seed, wide).items(), start=1):
        cols = list(dict.fromkeys(k for r in rows for k in r)) or ["id"]
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(cols)
        w.writerows([r.get(c, "") for c in cols] for r in rows)
        (dbdir / f"{i:03d}_dbo.{name}.csv").write_bytes(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"))
        man.append({"index": f"01/01/{i:03d}", "instance": "PC", "database": "stock", "schema": "dbo", "table": name,
                    "rows": str(len(rows)), "file": f"01_PC\\01_stock\\{i:03d}_dbo.{name}.csv"})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(man[0]), quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writeheader()
    w.writerows(man)
    (root / "manifest.csv").write_bytes(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"))
    (folder / "summary.txt").write_text(f"RESULT: PASS - {len(man)} tables (synthetic)\n", encoding="utf-8-sig")
    return folder


_STEP = r"""
import json, sys, time, runpy
t = time.perf_counter()
code = 0
try:
    sys.argv = ["manage.py"] + json.loads(sys.argv[1])
    runpy.run_path("manage.py", run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
peak = None
try:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024        # MB (Linux reports KB)
except ImportError:
    try:
        import ctypes, ctypes.wintypes as w
        class C(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t)] + [(f"x{i}", ctypes.c_size_t) for i in range(6)]
        c = C(); c.cb = ctypes.sizeof(C)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        peak = c.PeakWorkingSetSize // (1024 * 1024)
    except Exception:
        pass
sys.stderr.write("\nBENCH " + json.dumps({"seconds": round(time.perf_counter() - t, 1), "peak_mb": peak, "exit": code}) + "\n")
"""


def bench(scale: float = 1.0, keep: Path | None = None, repo: Path | None = None) -> dict:
    """Write the export, then --status, the dry run and --apply as separate processes on a new pharmacy."""
    repo = Path(repo or Path(__file__).resolve().parents[2])
    work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="estock-bench-"))
    work.mkdir(parents=True, exist_ok=True)
    out = {"sizes": sizes(scale), "steps": {}}
    try:
        t = time.perf_counter()
        reads = work / "estock-db-read"
        # in its own process: a child inherits its parent's peak memory on Linux, so this process stays small
        subprocess.run([sys.executable, "-m", "app.dataimport.estock_synthetic", "--write", str(reads / "20261118-220000"),
                        "--scale", str(scale)], cwd=repo, check=True, capture_output=True)
        out["steps"]["write synthetic export"] = {"seconds": round(time.perf_counter() - t, 1)}
        out["export_mb"] = round(sum(f.stat().st_size for f in reads.rglob("*") if f.is_file()) / 2 ** 20, 1)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHARM_")}
        env.update({"PHARM_DATA_DIR": str(work / "data"), "PYTHONIOENCODING": "utf-8"})
        pw = "Bench-Owner-2026"
        init = ("import sys; sys.path.insert(0, '.'); from app.core import accounts; from app.db import Database; "
                "from pathlib import Path; import os; d = Path(os.environ['PHARM_DATA_DIR']); d.mkdir(parents=True, exist_ok=True); "
                "db = Database(d / 'pharmacy.db'); db.migrate(); "
                f"accounts.create_organization(db, name='صيدلية تجريبية', owner_username='owner', owner_full_name='Owner', owner_password='{pw}')")
        subprocess.run([sys.executable, "-c", init], cwd=repo, env=env, check=True, capture_output=True)
        common = ["import-estock-db", "--dir", str(reads)]
        login = ["--username", "owner", "--password", pw]
        for name, args in (("status (is the read complete)", common + ["--status"]),
                           ("dry run (copy + move + reports)", common + login + ["--out", str(work / "dry")]),
                           ("apply (backup + move + archive)", common + login + ["--out", str(work / "apply"), "--apply"])):
            r = subprocess.run([sys.executable, "-c", _STEP, json.dumps(args)], cwd=repo, env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            line = [x for x in r.stderr.splitlines() if x.startswith("BENCH ")]
            res = json.loads(line[-1][6:]) if line else {"exit": r.returncode}
            res["ok"] = "RECONCILIATION: OK" in r.stdout if "status" not in name else "READ: COMPLETE" in r.stdout
            if not res["ok"]:
                res["tail"] = (r.stdout + r.stderr)[-3000:]
            out["steps"][name] = res
        db = work / "data" / "pharmacy.db"
        out["db_mb"] = round(db.stat().st_size / 2 ** 20, 1) if db.exists() else None
        arch = work / "data" / "estock-archive.sqlite"
        out["archive_mb"] = round(arch.stat().st_size / 2 ** 20, 1) if arch.exists() else None
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--write", metavar="FOLDER")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--keep", metavar="FOLDER", help="bench: keep the work folder here")
    a = ap.parse_args(argv)
    if a.write:
        print(write(Path(a.write), a.scale))
        return 0
    if not a.bench:
        ap.print_help()
        return 2
    res = bench(a.scale, Path(a.keep) if a.keep else None)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if all(s.get("ok", True) for s in res["steps"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
