"""Full move from the E-Stock SQL Server database (F91), from the CSV export of estock-db-read.bat.

Runs on the pharmacy PC only; real data never leaves it. Steps (each one atomic, resumable by its
import_runs row): items -> suppliers -> customers -> opening stock -> owner-confirmed balance
adjustments; the history goes to a separate read-only archive (estock_archive.py).

Nothing is decided silently. Every value E-Stock holds that cannot be true (negative cost, broken unit
conversions, a 9,999,999,999,999 credit limit, ...) is a data-quality row: E-Stock value and what was
done with it. A value that cannot be moved without a person's decision is a *blocking* row, and the
reconciliation fails while any is left.

Rules (each recorded in the report):
- pack size = units of the smallest unit E-Stock defines for the item (product_unit1_3 when unit 3 is
  set, else product_unit1_2 when unit 2 is set, else 1), both read as "per unit 1" (the column names);
  the stock fractions must fit it (E-Stock drift up to 0.15 unit is rounded and listed), else the item
  needs a decision in --packs;
- the live stock table (Product_Amount or Branches_Product_Amount, per branch) is the one whose rows
  E-Stock touched after the latest sales; when that is not clear the run stops and asks for
  --stock-source, it never picks one;
- a negative stock row (E-Stock sold more than it had of that expiry) is netted against the item's other
  rows, earliest expiry first, so the item total stays E-Stock's; an item whose total is negative starts at 0;
- balances: customer_current_money (+ = the customer owes us) and vendor_current_money (+ = we owe the
  supplier) are the opening balances; with --confirm the owner's confirmed amount wins and the
  difference is an opening *adjustment* ledger row, never a payment or an expense.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from ..catalog import matching, service as catalog
from ..catalog.normalize import normalize_text
from ..core import audit
from ..core.errors import Conflict, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory

SOURCE = "estock_db"
COST_Q = Decimal("0.000001")
DRIFT = Decimal("0.15")          # E-Stock rounding drift on split items, in base units (as estock_job)
NOISE = Decimal("0.000001")      # float noise of the export (G17), in base units
HALF_PIASTER = Decimal("0.005")
MAX_REAL_LIMIT = Decimal("1000000")       # a credit limit above 1,000,000 EGP is not a real limit
BARCODE_COLS = ("product_int_code", "product_int_code1", "product_int_code2", "product_int_code3",
                "product_int_code4")
STOCK_TABLES = ("Product_Amount", "Branches_Product_Amount")
HISTORY_TABLES = ("Sales_header", "Branches_sales_details", "Back_sales_header", "Back_Sales_details",
                  "Purchase_header", "Purchase_details", "Branches_purchase_details", "Gedo_customers",
                  "Gedo_Vendors", "Shortcoming", "Cash_disk_close")
DEFAULT_STORE = "الصيدلية"


class ExportError(ValidationFailed):
    pass


# ---------------------------------------------------------------- reading the export
csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))


def read_summary(run: Path) -> dict:
    """What estock-db-read.ps1 says about one run (summary.txt): its result, whether it was a retry of
    another run or a partial read (-Only), and the tables it could not read ("db.schema.table", lower
    case; a table whose counts alone failed is still exported and is not listed)."""
    out = {"name": run.name, "result": None, "retry_of": None, "only": False, "failed": []}
    f = run / "summary.txt"
    if not f.is_file():
        return out
    in_list = False
    for line in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("retry of run "):
            out["retry_of"] = line[len("retry of run "):].split(":", 1)[0].strip()
        elif line.startswith("only: "):
            out["only"] = True
        if line.startswith("RESULT: "):
            out["result"] = line[8:].split(" ", 1)[0]
            in_list = line.startswith("RESULT: PARTIAL")
            continue
        if not in_list:
            continue
        if not line.startswith("   "):
            in_list = False
            continue
        e = line.strip()
        if " / " not in e:
            continue                              # an instance that could not sign in
        e = e.split(" / ", 1)[1]
        if e.endswith(" (stats)"):
            continue
        out["failed"].append(e.lower())           # "db.schema.table", or "db (why)" for a whole database
    return out


def resolve_runs(root: Path) -> tuple[list[Path], list[str]]:
    """The newest full read under estock-db-read\ and the retry runs made for it (estock-db-read-one.bat),
    oldest first, and the tables still not read after them all."""
    runs = sorted((d for d in Path(root).iterdir() if d.is_dir() and (d / "summary.txt").is_file()),
                  key=lambda d: d.name)
    info = {d.name: read_summary(d) for d in runs}
    full = [d for d in runs if not info[d.name]["retry_of"] and not info[d.name]["only"]
            and (d / "tables" / "manifest.csv").is_file()]
    if not full:
        raise ExportError(f"مفيش قراية كاملة لإي ستوك في {root}: شغّل estock-db-read.bat الأول")
    base = full[-1]
    chain, names = [base], {base.name}
    for d in runs:
        if d.name > base.name and info[d.name]["retry_of"] in names:
            chain.append(d)
            names.add(d.name)
    missing = set(info[base.name]["failed"])
    for d in chain[1:]:
        man = d / "tables" / "manifest.csv"
        if man.is_file():
            with open(man, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
                    missing.discard(f"{r.get('database', '')}.{r.get('schema', '')}.{r.get('table', '')}".lower())
    return [d if (d / "manifest.csv").is_file() else d / "tables" for d in chain], sorted(missing)


class Export:
    """manifest.csv + one CSV per table (estock-db-read.ps1): UTF-8 with BOM, a header row, every cell
    text; NULL is empty, dates are SQL Server style 126, byte[] is b64:...

    `folder` is one read (the folder with tables\manifest.csv), or estock-db-read\ itself: then the newest
    full read is taken together with the retry runs made for it, a table read again replacing the first copy;
    tables still not read are in `missing` (and block the move)."""

    def __init__(self, folder: Path, database: str | None = None):
        folder = Path(folder)
        cands = [folder / "manifest.csv", folder / "tables" / "manifest.csv"]
        man = next((p for p in cands if p.is_file()), None)
        if man:
            dirs = [man.parent]
            run = man.parent.parent if man.parent.name == "tables" else man.parent
            missing = read_summary(run)["failed"]
        elif folder.is_dir() and any((d / "summary.txt").is_file() for d in folder.iterdir() if d.is_dir()):
            dirs, missing = resolve_runs(folder)
        else:
            raise ExportError(f"manifest.csv مش موجود في {folder} (ولا في tables جواه)")
        self.runs = [(d.parent.name if d.name == "tables" else d.name) for d in dirs]
        self.tables_dir = dirs[0]
        rows = []
        for n, d in enumerate(dirs):
            with open(d / "manifest.csv", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
                    r["_dir"], r["_n"] = d, n
                    rows.append(r)
        groups: dict[tuple, list] = defaultdict(list)
        for r in rows:
            groups[(r.get("instance", ""), r.get("database", ""))].append(r)
        want = (database or "stock").lower()
        pick = [g for g in groups if g[1].lower() == want]
        if not pick and not database:
            pick = [g for g, t in groups.items() if any(x.get("table", "").lower() == "products" for x in t)]
        if len(pick) != 1:
            raise ExportError("مش واضح أنهي قاعدة بيانات هي إي ستوك: "
                              + ", ".join(f"{i}/{d}" for i, d in groups) + " (استخدم --database)")
        self.instance, self.database = pick[0]
        self.tables: dict[str, dict] = {}
        # the dbo copy first; among runs, the latest read of a table wins (a retry replaces the first try)
        for r in sorted(groups[pick[0]], key=lambda r: (r.get("schema", "").lower() != "dbo", -r["_n"])):
            key = r["table"].lower()
            if key in self.tables:
                continue
            self.tables[key] = {"name": r["table"], "schema": r.get("schema", ""),
                                "rows": int(r["rows"]) if r.get("rows", "").isdigit() else None,
                                "path": r["_dir"] / r["file"].replace("\\", "/")}
        db = self.database.lower()
        self.missing = [m for m in missing if m.split(".", 1)[0].split(" (", 1)[0] == db]
        self._cache: dict[str, list[dict]] = {}
        self._raw: dict[str, tuple[list[str], list[list[str]]]] = {}

    def has(self, name: str) -> bool:
        return name.lower() in self.tables

    def raw(self, name: str) -> tuple[list[str], list[list[str]]]:
        """Header + rows exactly as in the file (for the archive)."""
        key = name.lower()
        if key not in self._raw:
            t = self.tables.get(key)
            if not t:
                raise ExportError(f"الجدول {name} مش في التصدير")
            if not t["path"].is_file():
                raise ExportError(f"ملف الجدول {name} مش موجود: {t['path']}")
            with open(t["path"], encoding="utf-8-sig", newline="") as f:
                rd = csv.reader(f)
                head = next(rd, [])
                self._raw[key] = (head, [r for r in rd])
        return self._raw[key]

    def rows(self, name: str) -> list[dict]:
        """Rows as dicts with lower-case column names (E-Stock mixes PV_id / pv_id)."""
        key = name.lower()
        if key not in self._cache:
            if key not in self.tables:
                self._cache[key] = []
            else:
                head, data = self.raw(name)
                cols = [h.strip().lower() for h in head]
                self._cache[key] = [dict(zip(cols, r)) for r in data]
        return self._cache[key]

    def sha(self, *names: str) -> str:
        h = hashlib.sha256()
        for n in names:
            t = self.tables.get(n.lower())
            h.update(n.lower().encode())
            if t and t["path"].is_file():
                h.update(hashlib.sha256(t["path"].read_bytes()).digest())
        return h.hexdigest()

    def source_sha(self) -> str:
        return self.sha(*sorted(t["name"] for t in self.tables.values()))


def dec(v) -> Decimal | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def piasters(d: Decimal) -> int:
    """Rounded to the piaster: only for comparisons and for values listed in data-quality as rounded."""
    return int((d * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def exact_piasters(d: Decimal) -> int | None:
    """Money that is posted must already be whole piasters: 120.005 is None (a person decides), never 12001."""
    q = d * 100
    return int(q) if q == q.to_integral_value() else None


def iso_day(v) -> str | None:
    s = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


_TRUE = {"1", "true", "t", "y", "yes", "نعم"}
_FALSE = {"0", "false", "f", "n", "no", "لا", ""}


def flag(v) -> bool | None:
    s = str(v or "").strip().lower()
    return True if s in _TRUE else False if s in _FALSE else None


def ident(v) -> str:
    """E-Stock ids are decimal(18,0): '123' and '123.0' are the same id."""
    s = str(v or "").strip()
    d = dec(s)
    return str(int(d)) if d is not None and d == d.to_integral_value() else s


def egp(d: Decimal | int | None) -> str:
    return "" if d is None else str(Decimal(d).quantize(Decimal("0.01"), ROUND_HALF_UP))


# ---------------------------------------------------------------- the plan (no database)
@dataclass
class Plan:
    items: list[dict] = field(default_factory=list)
    stock: list[dict] = field(default_factory=list)
    suppliers: list[dict] = field(default_factory=list)
    customers: list[dict] = field(default_factory=list)
    dq: list[dict] = field(default_factory=list)
    per_item: dict = field(default_factory=dict)      # product_id -> raw / listed / planned totals
    stock_source: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)

    def issue(self, table, eid, code, fld, value, action, *, value_egp=None, block=False, kind="fixed"):
        self.dq.append({"table": table, "estock_id": eid, "code": code, "field": fld, "estock_value": str(value),
                        "action": action, "value_egp": egp(value_egp) if value_egp is not None else "",
                        "severity": "block" if block else kind})

    @property
    def blocking(self) -> list[dict]:
        return [d for d in self.dq if d["severity"] == "block"]


def build_plan(ex: Export, *, packs: dict[str, int] | None = None, stock_source: str | None = None,
               stores: list[str] | None = None, confirm: dict | None = None) -> Plan:
    p = Plan(rules={"stores": stores or [DEFAULT_STORE], "stock_source": stock_source,
                    "packs_overrides": len(packs or {}), "confirm_rows": len((confirm or {}).get("rows", {}))})
    for t in ("Products", "Customer", "Vendor"):
        if not ex.has(t):
            raise ExportError(f"الجدول {t} مش في التصدير؛ شغّل estock-db-read.bat تاني")
    for m in ex.missing:        # a table E-Stock did not give: the move would not be complete
        p.issue("export", "", "", "table not read", m,
                "الجدول ماتقراش من إي ستوك: estock-db-import.bat بيعيد قرايته لوحده (أو estock-db-read-one.bat)",
                block=True)
    _plan_items(ex, p, packs or {})
    _plan_stock(ex, p, stock_source, stores or [DEFAULT_STORE])
    _plan_parties(ex, p, confirm or {})
    _plan_history_checks(ex, p)
    _plan_money_info(ex, p)
    return p


def _plan_items(ex: Export, p: Plan, packs: dict[str, int]) -> None:
    companies = {ident(r.get("company_id")): (r.get("co_name_ar") or r.get("co_name_en") or "").strip()
                 for r in ex.rows("Companys")}
    prods = sorted(ex.rows("Products"), key=lambda r: (dec(r.get("product_id")) or 0))
    real_codes = defaultdict(list)
    for r in prods:
        c = (r.get("product_code") or "").strip()
        if c:
            real_codes[c].append(ident(r.get("product_id")))
    taken_bc: dict[str, str] = {}
    used_codes: set[str] = set()
    for r in prods:
        pid = ident(r.get("product_id"))
        code = (r.get("product_code") or "").strip()
        if not code or len(real_codes[code]) > 1 and real_codes[code][0] != pid:
            new = f"E{pid}"
            while new in real_codes or new in used_codes:
                new += "x"
            p.issue("Products", pid, code, "product_code", code or "(فاضي)",
                    f"{'كود مكرر' if code else 'من غير كود'}: الكود في البرنامج {new}")
            code = new
        used_codes.add(code)
        name_en = (r.get("product_name_en") or "").strip() or None
        name_ar = (r.get("product_name_ar") or "").strip() or None
        if not name_en and not name_ar:
            name_ar = f"صنف بدون اسم {code}"
            p.issue("Products", pid, code, "product_name", "(فاضي)", f"الاسم في البرنامج: {name_ar}")
        it = {"product_id": pid, "code": code, "name_en": name_en, "name_ar": name_ar,
              "active_ingredient": (r.get("product_scientific_name") or "").strip() or None,
              "manufacturer": companies.get(ident(r.get("company_id"))) or None, "barcodes": []}
        drug = flag(r.get("product_drug"))
        if drug is None:
            p.issue("Products", pid, code, "product_drug", r.get("product_drug"), "دواء/غير دواء: غير معروف",
                    kind="info")
        it["is_drug"] = None if drug is None else int(drug)
        deleted, active = flag(r.get("deleted")), flag(r.get("active"))
        it["inactive"] = bool(deleted) or active is False
        price = dec(r.get("sell_price"))
        if price is None or price <= 0:
            p.issue("Products", pid, code, "sell_price", r.get("sell_price") or "(فاضي)",
                    "سعر الجمهور اتساب فاضي (يتحدد من شاشة الصنف أو أول فاتورة شراء)", kind="info" if price == 0 else "fixed")
            it["public_price"] = None
        else:
            it["public_price"] = piasters(price)
            if exact_piasters(price) is None:
                p.issue("Products", pid, code, "sell_price", r.get("sell_price"),
                        f"كسر قرش في السعر: اتقرّب لـ {egp(Decimal(it['public_price']) / 100)}")
        for col in ("product_disc1", "product_disc2"):
            d = dec(r.get(col))
            if d is not None and not 0 <= d <= 100:
                p.issue("Products", pid, code, col, r.get(col), "خصم مستحيل: ما اتنقلش (البرنامج مابينقلش خصم الصنف)")
        for col in ("buy_price", "unit2_sell_price", "unit3_sell_price", "sell_clause", "unit2_sell_price_clause",
                    "unit3_sell_price_clause"):
            d = dec(r.get(col))
            if d is not None and d < 0:
                p.issue("Products", pid, code, col, r.get(col), "قيمة بالسالب: ما اتنقلتش (التكلفة من جدول الرصيد)")
        for col in BARCODE_COLS:
            raw = (r.get(col) or "").strip()
            if not raw:
                continue
            bc = raw[:-2] if raw.endswith(".0") else raw
            if not bc.isdigit() or not 6 <= len(bc) <= 14:
                p.issue("Products", pid, code, col, raw, "مش باركود (لازم 6 لـ 14 رقم): ما اتضافش")
                continue
            if bc in taken_bc:
                if taken_bc[bc] != pid:
                    p.issue("Products", pid, code, col, raw, f"الباركود متسجل لصنف تاني (E-Stock {taken_bc[bc]}): اتساب للأول")
                continue
            taken_bc[bc] = pid
            it["barcodes"].append(bc)
        first = len(p.dq)
        it["pack_size"], it["pack_unknown"] = _pack_of(r, p, pid, code)
        if code in packs:
            for d in p.dq[first:]:          # the person's decision settles the broken conversion
                if d["severity"] == "block":
                    d["severity"], d["action"] = "fixed", d["action"] + f" ← اتحدد من ملف القرار: {packs[code]}"
            if packs[code] != it["pack_size"] or it["pack_unknown"]:
                p.issue("Products", pid, code, "pack_size", it["pack_size"],
                        f"عدد الوحدات من ملف القرار: {packs[code]}", kind="fixed")
            it["pack_size"], it["pack_unknown"], it["pack_decided"] = packs[code], False, True
        p.items.append(it)


def _pack_of(r: dict, p: Plan, pid: str, code: str) -> tuple[int, bool]:
    factors = {}
    for n in ("2", "3"):
        unit = ident(r.get(f"product_unit{n}"))
        raw = r.get(f"product_unit1_{n}")
        f = dec(raw)
        if unit in ("", "0") and (f is None or f in (0, 1)):
            continue
        if f is None or f < 1 or f != f.to_integral_value():
            p.issue("Products", pid, code, f"product_unit1_{n}", raw or "(فاضي)",
                    "معامل الوحدة مش رقم صحيح موجب: عدد الوحدات محتاج قرار (--packs)", block=True)
            return 1, True
        factors[n] = int(f)
    f2, f3 = factors.get("2"), factors.get("3")
    if f2 and f3 and f3 > 1 and f2 > 1 and f3 % f2:
        p.issue("Products", pid, code, "product_unit1_3", f"{f3} (الوحدة 2: {f2})",
                "معامل الوحدة الصغرى مش مضاعف للوسطى: عدد الوحدات محتاج قرار (--packs)", block=True)
        return f3, True
    return (f3 if f3 and f3 > 1 else f2 if f2 and f2 > 1 else 1), False


def _store_ids(ex: Export, stores: list[str]) -> tuple[set[str], list[dict]]:
    allst = [{"id": ident(r.get("store_id")), "name": (r.get("store_name_ar") or r.get("store_name_en") or "").strip()}
             for r in ex.rows("Stores")]
    want = {normalize_text(s).replace(" ", "") for s in stores}
    ids = {s["id"] for s in allst if normalize_text(s["name"]).replace(" ", "") in want or s["id"] in stores}
    return ids, allst


def _plan_stock(ex: Export, p: Plan, source: str | None, stores: list[str]) -> None:
    store_ids, allst = _store_ids(ex, stores)
    p.stock_source["stores"] = allst
    if not store_ids:
        raise ExportError("المخزن المطلوب مش موجود في إي ستوك. المخازن: " + "، ".join(f"{s['id']} {s['name']}" for s in allst))
    p.stock_source["chosen_stores"] = sorted(store_ids)
    groups = _stock_groups(ex)
    p.stock_source["candidates"] = [{k: v for k, v in g.items() if k != "rows"} for g in groups.values()]
    if source:
        key = source if ":" in source else None
        hits = [g for g in groups.values() if g["key"] == source or (not key and g["table"].lower() == source.lower())]
        if len(hits) != 1:
            raise ExportError(f"--stock-source {source} مش واحد من: " + ", ".join(groups))
        chosen, why = hits[0], "من --stock-source"
    else:
        ranked = sorted(groups.values(), key=lambda g: (g["fresh_hits"], g["last_change"]), reverse=True)
        best = ranked[0] if ranked else None
        second = ranked[1]["fresh_hits"] if len(ranked) > 1 else 0
        if not best or not best["checked"] or best["fresh_hits"] * 2 < best["checked"] or second * 2 > best["fresh_hits"]:
            p.stock_source["decision"] = None
            p.issue("stock", "", "", "stock_source", "; ".join(
                f"{g['key']}: {g['fresh_hits']}/{g['checked']}" for g in ranked),
                "مش واضح أنهي جدول رصيد هو الشغال: اختار بـ --stock-source بعد ما تقارن الإجماليات بتقرير إي ستوك",
                block=True)
            return
        chosen, why = best, (f"اتغيّر بعد آخر مبيعات في {best['fresh_hits']} من {best['checked']} صنف اتباع "
                             f"(التاني: {second})")
    p.stock_source["decision"] = {"key": chosen["key"], "why": why}
    items = {it["product_id"]: it for it in p.items}
    by_item: dict[str, list[dict]] = defaultdict(list)
    for r in chosen["rows"]:
        if ident(r.get("store_id")) not in store_ids:
            continue
        by_item[ident(r.get("product_id"))].append(r)
    for pid, rows in sorted(by_item.items(), key=lambda kv: (dec(kv[0]) or 0)):
        it = items.get(pid)
        tot = {"raw_units": Decimal(0), "raw_cost": Decimal(0), "listed_units": Decimal(0),
               "listed_cost": Decimal(0), "planned_units": 0, "planned_cost": Decimal(0),
               "code": it["code"] if it else pid, "name": (it["name_en"] or it["name_ar"]) if it else ""}
        p.per_item[pid] = tot
        if not it:
            v = sum((dec(r.get("amount")) or 0) * (dec(r.get("buy_price")) or 0) for r in rows)
            tot["raw_cost"] += v
            p.issue(chosen["table"], pid, pid, "product_id", pid, "رصيد لصنف مش موجود في جدول الأصناف", value_egp=v,
                    block=True)
            continue
        pack = it["pack_size"]
        pos, neg = [], []
        for r in rows:
            amt, bp = dec(r.get("amount")), dec(r.get("buy_price"))
            eid = ident(r.get("pa_id") or r.get("branch_pa_id"))
            if amt is None:
                p.issue(chosen["table"], eid, it["code"], "amount", r.get("amount"), "كمية مش رقم", block=True)
                continue
            if amt == 0:
                continue
            exact = amt * pack
            tot["raw_units"] += exact
            tot["raw_cost"] += amt * (bp or 0)
            exp = iso_day(r.get("exp_date"))
            if r.get("exp_date") and (not exp or not 2000 <= int(exp[:4]) <= 2100):
                p.issue(chosen["table"], eid, it["code"], "exp_date", r.get("exp_date"),
                        "تاريخ صلاحية مستحيل: اتنقل من غير صلاحية", kind="fixed")
                exp = None
            unit_cost = None
            if bp is not None and bp >= 0:
                unit_cost = (bp / pack).quantize(COST_Q, ROUND_HALF_UP)
            else:
                p.issue(chosen["table"], eid, it["code"], "buy_price", r.get("buy_price") or "(فاضي)",
                        "تكلفة بالسالب أو فاضية: اتنقل من غير تكلفة", value_egp=amt * (bp or 0))
                tot["listed_cost"] -= amt * (bp or 0)
            row = {"eid": eid, "product_id": pid, "exact": exact, "expiry": exp, "unit_cost": unit_cost,
                   "amount": amt, "bp": bp}
            if not exp and amt > 0:      # owner 18/11: a drug without expiry is reviewed; a non-drug needs none
                if it["is_drug"] == 0:
                    p.issue(chosen["table"], eid, it["code"], "expiry", "(فاضي)", "صنف مش دواء: عادي من غير صلاحية",
                            kind="info")
                else:
                    p.issue(chosen["table"], eid, it["code"], "expiry", "(فاضي)",
                            "دواء من غير صلاحية: راجعه" if it["is_drug"] else "مش معروف دواء ولا لا ومن غير صلاحية: راجعه",
                            value_egp=amt * (bp or 0), kind="review")
            (neg if amt < 0 else pos).append(row)
        for row in pos:
            whole = row["exact"].to_integral_value(ROUND_HALF_UP)
            diff = row["exact"] - whole
            tol = max(Decimal("0.0006") * pack, DRIFT) if pack > 1 else Decimal("0.0006")
            if abs(diff) > tol:
                p.issue(chosen["table"], row["eid"], it["code"], "amount", row["amount"],
                        f"الكمية مش عدد صحيح من الوحدات (العبوة {pack}): عدد الوحدات محتاج قرار (--packs)",
                        value_egp=row["amount"] * (row["bp"] or 0), block=True)
                row["units"] = 0
            else:
                row["units"] = int(whole)
                if abs(diff) > NOISE:
                    what = "أقل من نص وحدة: ما اتنقلش" if whole == 0 else f"اتقرّب لـ {int(whole)} وحدة (فرق تقريب إي ستوك)"
                    p.issue(chosen["table"], row["eid"], it["code"], "amount", row["amount"], what,
                            value_egp=-diff * (row["unit_cost"] or 0))
                    tot["listed_units"] -= diff
                    if row["unit_cost"] is not None:
                        tot["listed_cost"] -= diff * row["bp"] / pack
        tol = max(Decimal("0.0006") * pack, DRIFT) if pack > 1 else Decimal("0.0006")
        for row in neg:
            whole = (-row["exact"]).to_integral_value(ROUND_HALF_UP)
            if abs(-row["exact"] - whole) > tol:      # the same rule as a positive row: never round a deficit
                p.issue(chosen["table"], row["eid"], it["code"], "amount", row["amount"],
                        f"رصيد بالسالب مش عدد صحيح من الوحدات (العبوة {pack}): عدد الوحدات محتاج قرار (--packs)",
                        value_egp=row["amount"] * (row["bp"] or 0), block=True)
                continue
            owe = int(whole)
            tot["listed_units"] -= row["exact"]
            if row["unit_cost"] is not None:
                tot["listed_cost"] -= row["amount"] * row["bp"]
            took = []
            for other in sorted(pos, key=lambda x: (x["expiry"] or "9999", x["eid"])):
                if not owe:
                    break
                n = min(owe, other["units"])
                if n:
                    other["units"] -= n
                    owe -= n
                    took.append(n)
                    tot["listed_units"] -= n
                    if other["unit_cost"] is not None:     # at the unrounded source cost (#339 review)
                        tot["listed_cost"] -= n * other["bp"] / pack
            what = "رصيد بالسالب: اتخصم من تشغيلات الصنف التانية (الأقرب صلاحية الأول)"
            if owe:
                what = "رصيد الصنف كله بالسالب: الصنف بيبدأ بصفر (يتعدّ في الجرد)"
            p.issue(chosen["table"], row["eid"], it["code"], "amount", row["amount"], what,
                    value_egp=row["amount"] * (row["bp"] or 0))
        for row in pos:
            if row["units"] > 0:
                p.stock.append(row)
                tot["planned_units"] += row["units"]
                tot["planned_cost"] += row["units"] * (row["unit_cost"] or 0)
    _unit_evidence(ex, p)


def _stock_groups(ex: Export) -> dict[str, dict]:
    """Each (table, branch) holding stock, with how fresh it is: of the items in the latest sales lines,
    how many have a stock row this group changed at or after that sale."""
    sales = sorted((r for r in ex.rows("Branches_sales_details") if r.get("insert_date")),
                   key=lambda r: r["insert_date"], reverse=True)[:300]
    last_sold: dict[str, str] = {}
    for r in sales:
        last_sold.setdefault(ident(r.get("product_id")), r["insert_date"])
    groups: dict[str, dict] = {}
    for t in STOCK_TABLES:
        if not ex.has(t):
            continue
        for r in ex.rows(t):
            key = f"{ex.tables[t.lower()]['name']}:{ident(r.get('branch_id'))}"
            g = groups.setdefault(key, {"key": key, "table": ex.tables[t.lower()]["name"],
                                        "branch_id": ident(r.get("branch_id")), "rows": [], "touch": {},
                                        "units_packs": Decimal(0), "cost": Decimal(0), "last_change": ""})
            g["rows"].append(r)
            amt, bp = dec(r.get("amount")) or 0, dec(r.get("buy_price")) or 0
            g["units_packs"] += amt
            g["cost"] += amt * bp
            ch = max(r.get("update_date") or "", r.get("insert_date") or "")
            pid = ident(r.get("product_id"))
            g["touch"][pid] = max(g["touch"].get(pid, ""), ch)
            g["last_change"] = max(g["last_change"], ch)
    for g in groups.values():
        g["checked"] = len(last_sold)
        g["fresh_hits"] = sum(1 for pid, at in last_sold.items() if g["touch"].get(pid, "") >= at)
        g["row_count"] = len(g["rows"])
        g["units_packs"], g["cost"] = str(g["units_packs"]), egp(g["cost"])
        del g["touch"]
    return groups


def _unit_evidence(ex: Export, p: Plan) -> None:
    """The sales lines say how many base units a sold unit had (sale_unit_change). A factor that does not
    divide the pack size disagrees with the rule: listed for a look, it does not stop the move."""
    items = {it["product_id"]: it for it in p.items}
    seen = set()
    for r in ex.rows("Branches_sales_details"):
        pid, f = ident(r.get("product_id")), dec(r.get("sale_unit_change"))
        it = items.get(pid)
        if not it or f is None or f <= 1 or f != f.to_integral_value() or (pid, f) in seen:
            continue
        seen.add((pid, f))
        if it["pack_size"] % int(f):
            p.issue("Branches_sales_details", pid, it["code"], "sale_unit_change", f,
                    f"معامل البيع مش بيقسم عدد الوحدات ({it['pack_size']}): راجع كارت الصنف", kind="info")


def _phones(raw: list[str]) -> tuple[list[str], list[str]]:
    from ..sales.customers import clean_phone
    ok, bad = [], []
    for v in raw:
        for part in re.split(r"[/,;]| {2,}", v or ""):
            part = part.strip()
            if not part:
                continue
            try:
                ph = clean_phone(part)
            except ValidationFailed:
                bad.append(part)
                continue
            if ph and len(ph) == 10 and ph[0] == "1":
                ph = "0" + ph
            if ph and len(ph) >= 7 and ph not in ok:
                ok.append(ph)
            elif ph and len(ph) < 7:
                bad.append(part)
    return ok, bad


def _plan_parties(ex: Export, p: Plan, confirm: dict) -> None:
    crows = confirm.get("rows", {})
    used_phones: dict[str, str] = {}
    for r in sorted(ex.rows("Customer"), key=lambda r: (dec(r.get("customer_id")) or 0)):
        cid = ident(r.get("customer_id"))
        code = (r.get("customer_code") or "").strip()
        name = (r.get("customer_name_ar") or "").strip() or (r.get("customer_name_en") or "").strip()
        if not name:
            name = f"عميل {code or cid}"
            p.issue("Customer", cid, code, "customer_name", "(فاضي)", f"الاسم في البرنامج: {name}")
        alt = (r.get("customer_name_en") or "").strip() if (r.get("customer_name_ar") or "").strip() else ""
        phones, bad = _phones([r.get("mobile"), r.get("tel")])
        notes = [f"كود إي ستوك: {code or cid}"]
        for ph in list(phones):
            if ph in used_phones:
                p.issue("Customer", cid, code, "mobile/tel", ph, f"الرقم متسجل لعميل تاني (E-Stock {used_phones[ph]}): اتكتب في الملاحظات")
                notes.append(f"رقم مكرر: {ph}")
                phones.remove(ph)
        for b in bad:
            p.issue("Customer", cid, code, "mobile/tel", b, "رقم مش صحيح: اتكتب في الملاحظات")
            notes.append(f"رقم: {b}")
        for ph in phones[3:]:
            notes.append(f"رقم: {ph}")
        phones = phones[:3]
        for ph in phones:
            used_phones[ph] = cid
        for col, label in (("job_id", "الوظيفة"), ("customer_house_no", "رقم البيت"),
                           ("customer_insurance_code", "كود التأمين"), ("contract_id", "تعاقد إي ستوك")):
            v = (r.get(col) or "").strip()
            if v and v != "0":
                notes.append(f"{label}: {v}")
        bal_raw = dec(r.get("customer_current_money"))
        if r.get("customer_current_money") and bal_raw is None:
            p.issue("Customer", cid, code, "customer_current_money", r.get("customer_current_money"), "رصيد مش رقم", block=True)
        bal = exact_piasters(bal_raw) if bal_raw is not None else 0
        if bal is None:
            p.issue("Customer", cid, code, "customer_current_money", r.get("customer_current_money"),
                    "الرصيد فيه كسر قرش: محتاج قرار (اكتب الرصيد المؤكد)", block=True)
            bal = piasters(bal_raw)
        lim_raw = dec(r.get("customer_max_money"))
        limit = 0
        if lim_raw is not None and 0 < lim_raw <= MAX_REAL_LIMIT:
            limit = piasters(lim_raw)
            if exact_piasters(lim_raw) is None:
                p.issue("Customer", cid, code, "customer_max_money", r.get("customer_max_money"),
                        f"كسر قرش في حد الآجل: اتقرّب لـ {egp(Decimal(limit) / 100)}")
        elif lim_raw is not None and lim_raw != 0:
            p.issue("Customer", cid, code, "customer_max_money", r.get("customer_max_money"),
                    "حد آجل مش حقيقي: اتحط صفر (مفيش آجل) لحد ما يتكتب في عمود «حد الآجل المؤكد» أو في شاشة العميل")
        c = crows.get(("customer", cid), {})
        p.customers.append(_party(p, "customer", cid, code, name, bal, c, limit=limit, alt=alt or None,
                                  estock_limit=(r.get("customer_max_money") or "").strip(),
                                  phones=phones, notes="؛ ".join(notes),
                                  address=(r.get("address") or "").strip() or None,
                                  inactive=bool(flag(r.get("deleted"))) or flag(r.get("active")) is False))
    names: dict[str, str] = {}
    for r in sorted(ex.rows("Vendor"), key=lambda r: (dec(r.get("vendor_id")) or 0)):
        vid = ident(r.get("vendor_id"))
        code = (r.get("vendor_code") or "").strip()
        name = (r.get("vendor_name_ar") or "").strip() or (r.get("vendor_name_en") or "").strip() or f"مورد {code or vid}"
        if normalize_text(name) in names:
            new = f"{name} ({code or vid})"
            p.issue("Vendor", vid, code, "vendor_name", name, f"نفس اسم مورد تاني (E-Stock {names[normalize_text(name)]}): اتسمى {new}")
            name = new
        names[normalize_text(name)] = vid
        phones, bad = _phones([r.get("mobile"), r.get("tel")])
        contacts = []
        for col in ("emp_tel", "emp_area_manegar", "emp_deliv", "emp_get_money"):
            v, det = (r.get(col) or "").strip(), (r.get(col + "_details") or "").strip()
            if v or det:
                contacts.append(f"{col}: {v} {det}".strip())
        for col in ("address", "company_code", "ven_notes", "ven_return"):
            if (r.get(col) or "").strip():
                contacts.append(f"{col}: {r[col].strip()}")
        bal_raw = dec(r.get("vendor_current_money"))
        if r.get("vendor_current_money") and bal_raw is None:
            p.issue("Vendor", vid, code, "vendor_current_money", r.get("vendor_current_money"), "رصيد مش رقم", block=True)
        vbal = exact_piasters(bal_raw) if bal_raw is not None else 0
        if vbal is None:
            p.issue("Vendor", vid, code, "vendor_current_money", r.get("vendor_current_money"),
                    "الرصيد فيه كسر قرش: محتاج قرار (اكتب الرصيد المؤكد)", block=True)
            vbal = piasters(bal_raw)
        c = crows.get(("supplier", vid), {})
        p.suppliers.append(_party(p, "supplier", vid, code, name, vbal, c,
                                  phone=" / ".join(phones + bad) or None, contacts=contacts,
                                  inactive=bool(flag(r.get("deleted"))) or flag(r.get("active")) is False))
    _balance_sign_check(ex, p)


def _party(p: Plan, kind: str, eid: str, code: str, name: str, bal: int, conf: dict, **more) -> dict:
    d = {"kind": kind, "estock_id": eid, "code": code, "name": name, "estock_balance": bal,
         "balance": bal, "adjustment": 0, **more}
    if conf and conf.get("seen") != bal:
        p.issue("Customer" if kind == "customer" else "Vendor", eid, code, "confirmed balance",
                f"وقت التأكيد {egp(Decimal(conf.get('seen') or 0) / 100)}، دلوقتي {egp(Decimal(bal) / 100)}",
                "الرصيد في إي ستوك اتغيّر بعد ما المالك أكده: محتاج تأكيد جديد على الملف الجديد",
                value_egp=Decimal(abs(bal - (conf.get("seen") or 0))) / 100, block=True)
        conf = {}
    if conf.get("balance") is not None:
        d["balance"] = conf["balance"]
        d["adjustment"] = conf["balance"] - bal
        if d["adjustment"]:
            p.issue("Customer" if kind == "customer" else "Vendor", eid, code, "confirmed balance",
                    egp(Decimal(bal) / 100), f"المالك أكد {egp(Decimal(conf['balance']) / 100)}: الفرق تسوية افتتاحية",
                    value_egp=Decimal(d["adjustment"]) / 100, kind="info")
    if kind == "customer" and conf.get("limit") is not None:
        d["limit"] = conf["limit"]
    if d.get("inactive") and d["balance"]:
        d["inactive"] = False
        p.issue("Customer" if kind == "customer" else "Vendor", eid, code, "deleted/active", "محذوف",
                "محذوف في إي ستوك وعليه رصيد: اتنقل نشط عشان الرصيد يبان", kind="info")
    return d


def _balance_sign_check(ex: Export, p: Plan) -> None:
    """Statement lines vs the balance: sum(for_me - for_him) should be the balance (+ = owes us for a
    customer; for a supplier the sides are mirrored). Counted both ways; if the opposite sign wins, every
    balance would be posted the wrong way round, so the run stops."""
    out = {}
    for kind, table, idcol, parties, me, him in (
            ("customers", "Gedo_customers", "customer_id", p.customers, "gc_for_me", "gc_for_him"),
            ("suppliers", "Gedo_Vendors", "vendor_id", p.suppliers, "gv_for_him", "gv_for_me")):
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for r in ex.rows(table):
            sums[ident(r.get(idcol))] += (dec(r.get(me)) or 0) - (dec(r.get(him)) or 0)
        same = opposite = checked = 0
        for d in parties:
            if not d["estock_balance"] or d["estock_id"] not in sums:
                continue
            checked += 1
            s = piasters(sums[d["estock_id"]])
            same += s == d["estock_balance"]
            opposite += s == -d["estock_balance"]
        out[kind] = {"checked": checked, "statement_matches": same, "statement_matches_negated": opposite}
        if opposite > same:
            p.issue(table, "", "", "balance sign", f"{same} مطابق / {opposite} بالعكس",
                    "كشف الحساب بيقول إن إشارة الرصيد بالعكس: محتاج مراجعة قبل النقل", block=True)
    p.checks["balance_sign"] = out


def _plan_history_checks(ex: Export, p: Plan) -> None:
    items = {it["product_id"] for it in p.items}
    for r in ex.rows("Shortcoming"):
        pid = ident(r.get("product_id"))
        if pid and pid not in items:
            p.issue("Shortcoming", ident(r.get("id")), pid, "product_id", pid,
                    "النواقص: الصنف مش موجود (غالبًا باركود اتكتب مكان الكود): اتحفظ في الأرشيف زي ما هو", kind="info")
    for r in ex.rows("Sales_header"):
        v = dec(r.get("total_bill_net"))
        if v is not None and v < 0:
            p.issue("Sales_header", ident(r.get("sales_id")), "", "total_bill_net", r.get("total_bill_net"),
                    "فاتورة صافيها بالسالب: اتحفظت في الأرشيف زي ما هي", value_egp=v, kind="info")
    heads = {ident(r.get("sales_id")) for r in ex.rows("Sales_header")}
    orphan = sum(1 for r in ex.rows("Branches_sales_details") if ident(r.get("sales_id")) not in heads)
    p.checks["sales_lines_without_header"] = orphan


MONEY_TABLES = re.compile(r"gedo_fin|financ|cash|khazn|treasur|safe|bank|network|wallet|visa|insta|vodaf|mahfaz", re.I)
_NOT_MONEY_COL = re.compile(r"(^|_)(id|uid|code|no|num|number|flag|type|class|date|time|year|month|day)s?$", re.I)


def _plan_money_info(ex: Export, p: Plan) -> None:
    """Owner 18/11: E-Stock's treasury figure is not real cash (supplier and expense payments were not
    recorded against it) and the wallets (InstaPay, Vodafone Cash) hold balances too. Nothing here creates a
    cash balance: it is shown in the report, with its source table, for the money accounts (F105)."""
    out = {"note": "information only: no cash or wallet balance is created from E-Stock", "tables": [],
           "last_shift_close": [], "sales_by_network": []}
    for t in sorted(ex.tables.values(), key=lambda t: t["name"].lower()):
        if not MONEY_TABLES.search(t["name"]):
            continue
        rows = ex.rows(t["name"])
        sums = {}
        for col in (rows[0].keys() if rows else []):
            if _NOT_MONEY_COL.search(col):
                continue
            vals = [r.get(col) for r in rows if (r.get(col) or "").strip()]
            nums = [dec(v) for v in vals]
            if vals and all(n is not None for n in nums) and any(n != n.to_integral_value() for n in nums) \
                    or (vals and all(n is not None for n in nums) and any(k in col.lower() for k in
                                                                          ("money", "cash", "total", "for_", "value", "balance"))):
                sums[col] = egp(sum(nums))
        out["tables"].append({"table": t["name"], "rows": len(rows), "sums": sums})
    closes: dict[str, dict] = {}
    for r in ex.rows("Cash_disk_close"):
        k = ident(r.get("cdc_cash_id")) or "?"
        if (r.get("insert_date") or "") >= closes.get(k, {}).get("insert_date", ""):
            closes[k] = r
    for k, r in sorted(closes.items()):
        out["last_shift_close"].append({"source": "Cash_disk_close", "cash_id": k, "at": r.get("insert_date") or "",
                                        "system_cash": egp(dec(r.get("cdc_curr_cash"))),
                                        "counted_cash": egp(dec(r.get("cdc_act_cash")))})
    for table, sign in (("Sales_header", 1), ("Back_sales_header", -1)):
        tot: dict[str, Decimal] = defaultdict(Decimal)
        for r in ex.rows(table):
            v = dec(r.get("network_money"))
            if v:
                tot[ident(r.get("network_id")) or "?"] += sign * v
        for k, v in sorted(tot.items()):
            out["sales_by_network"].append({"source": f"{table}.network_money", "network_id": k, "total": egp(v)})
    p.checks["money_info"] = out


# ---------------------------------------------------------------- the owner's confirmation sheet
SHEET_HEAD = ["النوع", "رقم إي ستوك", "كود إي ستوك", "الاسم", "رصيد إي ستوك", "الرصيد المؤكد",
              "حد الآجل في إي ستوك", "حد الآجل المؤكد"]
_KIND_AR = {"supplier": "مورد", "customer": "عميل"}


def write_openings(plan: Plan, path: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "الأرصدة"
    ws.sheet_view.rightToLeft = True
    ws.append(SHEET_HEAD)
    rows = [d for d in plan.suppliers + plan.customers]
    rows.sort(key=lambda d: (-d["estock_balance"], d["kind"], d["name"]))
    for d in rows:
        ws.append([_KIND_AR[d["kind"]], d["estock_id"], d["code"], d["name"], d["estock_balance"] / 100, None,
                   d.get("estock_limit") if d["kind"] == "customer" else None, None])
    for col, w in zip("ABCDEFGH", (8, 12, 12, 36, 16, 16, 18, 18)):
        ws.column_dimensions[col].width = w
    from ..core.xlsx_safe import as_text
    as_text(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def read_confirm(path: Path) -> dict:
    from ..purchasing.importers import files
    sheets = files.read_excel_sheets(Path(path).read_bytes())
    rows = sheets[0][1] if sheets else []
    head = [str(c or "").strip() for c in (rows[0] if rows else [])]
    try:
        ik, ii, ie, ib, il = (head.index(h) for h in ("النوع", "رقم إي ستوك", "رصيد إي ستوك", "الرصيد المؤكد",
                                                      "حد الآجل المؤكد"))
    except ValueError:
        raise ValidationFailed("ملف التأكيد لازم يكون نفس ملف openings-to-confirm.xlsx (الأعمدة ناقصة)")
    kinds = {v: k for k, v in _KIND_AR.items()}
    out: dict = {}
    for n, r in enumerate(rows[1:], start=2):
        r = list(r) + [None] * len(head)
        kind = kinds.get(str(r[ik] or "").strip())
        if not kind:
            continue
        eid = ident(r[ii])
        got = {}
        for key, j in (("balance", ib), ("limit", il)):
            v = r[j]
            if v in (None, ""):
                continue
            d = dec(str(v).replace(",", ""))
            if d is None or (key == "limit" and d < 0) or exact_piasters(d) is None:
                raise ValidationFailed(f"ملف التأكيد سطر {n}: قيمة مش صحيحة «{v}» (جنيه وقروش بس)")
            got[key] = exact_piasters(d)
        if got:      # the E-Stock balance the owner saw: a later change makes the confirmation stale
            d = dec(str(r[ie] if r[ie] is not None else "").replace(",", ""))
            if d is None or exact_piasters(d) is None:
                raise ValidationFailed(f"ملف التأكيد سطر {n}: خانة «رصيد إي ستوك» فاضية أو مش رقم")
            got["seen"] = exact_piasters(d)
        if (kind, eid) in out:
            raise ValidationFailed(f"ملف التأكيد سطر {n}: {eid} مكرر")
        out[(kind, eid)] = got
    return {"rows": out, "sha": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


# ---------------------------------------------------------------- applying
def refusal(db: Database, ctx: Context, export_sha: str) -> str | None:
    """Only into a new database (launch night), or the same export again (resume after a failure)."""
    if db.one("SELECT 1 FROM sales WHERE org_id=? LIMIT 1", (ctx.org_id,)):
        return "قاعدة البيانات فيها فواتير بيع: النقل من إي ستوك يكون على قاعدة جديدة بس. مفيش أي حاجة اتكتبت"
    ours = "(SELECT id FROM import_runs WHERE org_id=? AND source_system='estock_db' AND status='applied')"
    if db.one("SELECT 1 FROM stock_movements WHERE org_id=? AND NOT (coalesce(ref_type,'')='import_run' AND"
              f" ref_id IN {ours}) LIMIT 1", (ctx.org_id, ctx.org_id)):
        return "قاعدة البيانات فيها حركات مخزون: النقل من إي ستوك يكون على قاعدة جديدة بس. مفيش أي حاجة اتكتبت"
    for table, what in (("customer_ledger", "حركات في حسابات العملاء"), ("supplier_ledger", "حركات في حسابات الموردين")):
        if db.one(f"SELECT 1 FROM {table} WHERE org_id=? AND coalesce(ref_id,'') NOT IN {ours} LIMIT 1",
                  (ctx.org_id, ctx.org_id)):
            return f"قاعدة البيانات فيها {what} مش من النقل ده: مفيش أي حاجة اتكتبت"
    for table in ("cash_movements", "purchase_invoices", "expenses"):
        if db.one(f"SELECT 1 FROM {table} WHERE org_id=? LIMIT 1", (ctx.org_id,)):
            return f"قاعدة البيانات فيها حركات فلوس ({table}): النقل من إي ستوك يكون على قاعدة جديدة بس. مفيش أي حاجة اتكتبت"
    for table in ("items", "customers", "suppliers"):
        if db.one(f"SELECT 1 FROM {table} t WHERE t.org_id=? AND t.id NOT IN (SELECT target_id FROM import_rows"
                  f" WHERE target_id IS NOT NULL AND run_id IN {ours}) LIMIT 1", (ctx.org_id, ctx.org_id)):
            return (f"قاعدة البيانات فيها بيانات ({table}) مش من النقل ده: النقل من إي ستوك يكون على قاعدة جديدة بس."
                    " مفيش أي حاجة اتكتبت")
    for r in db.all("SELECT options FROM import_runs WHERE org_id=? AND source_system='estock_db' AND status='applied'",
                    (ctx.org_id,)):
        o = json.loads(r["options"] or "{}")
        if o.get("export_sha") and o["export_sha"] != export_sha:
            return ("قاعدة البيانات فيها نقل من تصدير تاني لإي ستوك؛ نفس التصدير بس ينفع يكمّل. "
                    "مفيش أي حاجة اتكتبت")
    return None


def _step(db: Database, ctx: Context, kind: str, step: str, sha: str, export_sha: str, label: str, fn) -> tuple[str, bool]:
    done = db.one("SELECT id FROM import_runs WHERE org_id=? AND kind=? AND file_sha256=? AND status='applied'",
                  (ctx.org_id, kind, sha))
    if done:
        return done["id"], True
    for r in db.all("SELECT options FROM import_runs WHERE org_id=? AND kind=? AND source_system=? AND status='applied'",
                    (ctx.org_id, kind, SOURCE)):
        if json.loads(r["options"] or "{}").get("step") == step:
            raise Conflict(f"خطوة {step} اتطبقت قبل كده ببيانات مختلفة؛ مفيش أي حاجة اتكتبت", code="estock_db_step_differs")
    run_id = new_id()
    with db.transaction():
        db.execute("INSERT INTO import_runs (id, org_id, branch_id, kind, source_system, filename, file_sha256,"
                   " mapping, options, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (run_id, ctx.org_id, ctx.branch_id, kind, SOURCE, label[:200], sha, "{}",
                    json.dumps({"step": step, "export_sha": export_sha}), ctx.user_id))
        rows, report = fn(run_id)
        for n, (raw, target) in enumerate(rows, start=1):
            db.execute("INSERT INTO import_rows (id, run_id, row_no, raw, data, action, target_id) VALUES (?,?,?,?,?,?,?)",
                       (new_id(), run_id, n, json.dumps(raw, ensure_ascii=False, default=str), "{}", "create", target))
        from ..core.accounts import iso, utcnow
        db.execute("UPDATE import_runs SET status='applied', applied_by=?, applied_at=?, report=? WHERE id=?",
                   (ctx.user_id, iso(utcnow()), json.dumps(report, ensure_ascii=False, default=str), run_id))
        audit.record(db, ctx, "import.apply", "import_run", run_id, after={"source": SOURCE, "step": step, **report})
    return run_id, False


def _map(db: Database, run_id: str, key: str) -> dict[str, str]:
    return {json.loads(r["raw"])[key]: r["target_id"] for r in
            db.all("SELECT raw, target_id FROM import_rows WHERE run_id=?", (run_id,))}


def apply_plan(db: Database, ctx: Context, plan: Plan, ex: Export, *, packs_sha: str = "", confirm_sha: str = "") -> dict:
    ctx.require("items.import")
    ctx.require("items.write")
    ctx.require("inventory.adjust")
    ctx.require("suppliers.write")
    ctx.require("customers.manage")
    if plan.blocking:
        raise ValidationFailed(f"فيه {len(plan.blocking)} مشكلة محتاجة قرار قبل النقل (data-quality)", code="blocking")
    esha = ex.source_sha()
    why = refusal(db, ctx, esha)
    if why:
        raise Conflict(why, code="estock_db_refused")
    label = f"estock-db {ex.instance}/{ex.database}"
    runs, resumed = {}, []

    def H(*parts):
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    # 1) items
    items_sha = H("items", ex.sha("Products", "Companys"), packs_sha)

    def do_items(run_id):
        rows = []
        for it in plan.items:
            f = {k: it[k] for k in ("code", "name_en", "name_ar", "active_ingredient", "manufacturer", "is_drug",
                                     "pack_size", "public_price") if it.get(k) is not None}
            if it["inactive"]:
                f["status"] = "inactive"
            try:
                iid = catalog.create_item(db, ctx, f, force=True, source="import")
            except Conflict as e:
                if e.code != "duplicate_item":
                    raise
                iid = catalog.create_item(db, ctx, f, force=True, source="import", dedupe_suffix=it["code"])
            for bc in it["barcodes"]:
                catalog.add_barcode(db, ctx, iid, bc)
            rows.append(({"product_id": it["product_id"], "code": it["code"]}, iid))
        return rows, {"items": len(rows), "barcodes": sum(len(i["barcodes"]) for i in plan.items)}
    runs["items"], r = _step(db, ctx, "items", "items", items_sha, esha, label, do_items)
    resumed += ["items"] if r else []
    matching.invalidate(ctx.org_id)
    item_map = _map(db, runs["items"], "product_id")

    # 2) suppliers, 3) customers: E-Stock balances as the opening
    def do_parties(kind):
        def fn(run_id):
            rows, total = [], 0
            for d in (plan.suppliers if kind == "supplier" else plan.customers):
                if kind == "supplier":
                    pid = catalog.create_supplier(db, ctx, name=d["name"], phone=d.get("phone"))
                    if d["inactive"]:
                        db.execute("UPDATE suppliers SET is_active=0 WHERE id=? AND org_id=?", (pid, ctx.org_id))
                    if d["estock_balance"]:
                        db.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, ref_id, note,"
                                   " created_by) VALUES (?,?,?,?, 'opening', ?, 'رصيد افتتاحي من إي ستوك', ?)",
                                   (new_id(), ctx.org_id, pid, d["estock_balance"], run_id, ctx.user_id))
                else:
                    from ..sales.service import Sales
                    ph = d["phones"] + [None, None, None]
                    pid = Sales(db).create_customer(ctx, name=d["name"], phone=ph[0], phone2=ph[1], phone3=ph[2],
                                                    credit_limit=d["limit"], notes=d["notes"], name_alt=d["alt"],
                                                    address1=d["address"])
                    if d["inactive"]:
                        db.execute("UPDATE customers SET is_active=0 WHERE id=? AND org_id=?", (pid, ctx.org_id))
                    if d["estock_balance"]:
                        db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id, note,"
                                   " created_by) VALUES (?,?,?,?, 'adjustment', ?, 'رصيد افتتاحي من إي ستوك', ?)",
                                   (new_id(), ctx.org_id, pid, d["estock_balance"], run_id, ctx.user_id))
                total += d["estock_balance"]
                rows.append(({"estock_id": d["estock_id"], "code": d["code"]}, pid))
            return rows, {"parties": len(rows), "balance_total": total}
        return fn
    runs["suppliers"], r = _step(db, ctx, "suppliers", "suppliers", H("suppliers", ex.sha("Vendor")), esha, label,
                                 do_parties("supplier"))
    resumed += ["suppliers"] if r else []
    runs["customers"], r = _step(db, ctx, "customers", "customers", H("customers", ex.sha("Customer")), esha, label,
                                 do_parties("customer"))
    resumed += ["customers"] if r else []

    # 4) opening stock
    src = plan.stock_source["decision"]["key"]
    stock_sha = H("stock", ex.sha(src.split(":")[0]), src, ",".join(plan.stock_source["chosen_stores"]), items_sha)

    def do_stock(run_id):
        rows, units = [], 0
        for s in plan.stock:
            iid = item_map[s["product_id"]]
            bid = inventory.get_or_create_batch(db, ctx, iid, None, s["expiry"], "day" if s["expiry"] else None)
            inventory.post_movement(db, ctx, item_id=iid, qty_delta=s["units"], movement_type="opening", batch_id=bid,
                                    unit_cost=s["unit_cost"], ref_type="import_run", ref_id=run_id,
                                    ref_line_id=s["eid"], note="رصيد افتتاحي من إي ستوك")
            units += s["units"]
            rows.append(({"pa_id": s["eid"], "product_id": s["product_id"]}, iid))
        return rows, {"stock_rows": len(rows), "units": units, "source": src}
    runs["stock"], r = _step(db, ctx, "opening_stock", "stock", stock_sha, esha, label, do_stock)
    resumed += ["stock"] if r else []
    if not r:
        try:
            from ..recalls.service import Recalls
            Recalls(db).match_new_batches(ctx)
        except Exception:   # alerting must never undo an applied import
            import logging
            logging.getLogger("pharm.recalls").exception("recall matching failed")

    # 5) the owner's confirmed balances: difference = opening adjustment
    adj = [d for d in plan.suppliers + plan.customers if d["adjustment"] or (d["kind"] == "customer" and confirm_sha)]
    if confirm_sha:
        sup_map, cus_map = _map(db, runs["suppliers"], "estock_id"), _map(db, runs["customers"], "estock_id")

        def do_confirm(kind):
            def fn(run_id):
                rows, total = [], 0
                for d in adj:
                    if d["kind"] != kind:
                        continue
                    pid = (sup_map if kind == "supplier" else cus_map)[d["estock_id"]]
                    if kind == "customer":
                        cur = db.one("SELECT credit_limit FROM customers WHERE id=? AND org_id=?",
                                     (pid, ctx.org_id))["credit_limit"]
                        if d["limit"] != cur:
                            db.execute("UPDATE customers SET credit_limit=? WHERE id=? AND org_id=?", (d["limit"], pid, ctx.org_id))
                            audit.record(db, ctx, "customer.update", "customer", pid, before={"credit_limit": cur},
                                         after={"credit_limit": d["limit"]})
                    if d["adjustment"]:
                        table = "supplier_ledger" if kind == "supplier" else "customer_ledger"
                        col = "supplier_id" if kind == "supplier" else "customer_id"
                        db.execute(f"INSERT INTO {table} (id, org_id, {col}, amount, kind, ref_id, note, created_by)"
                                   " VALUES (?,?,?,?, 'adjustment', ?, 'تسوية افتتاحية: الرصيد اللي أكده المالك', ?)",
                                   (new_id(), ctx.org_id, pid, d["adjustment"], run_id, ctx.user_id))
                        total += d["adjustment"]
                    rows.append(({"estock_id": d["estock_id"], "adjustment": d["adjustment"]}, pid))
                return rows, {"adjusted": sum(1 for d in adj if d["kind"] == kind and d["adjustment"]),
                              "adjustment_total": total}
            return fn
        runs["confirm_suppliers"], r = _step(db, ctx, "suppliers", "confirm", H("confirm-s", confirm_sha, runs["suppliers"]),
                                             esha, label, do_confirm("supplier"))
        resumed += ["confirm_suppliers"] if r else []
        runs["confirm_customers"], r = _step(db, ctx, "customers", "confirm", H("confirm-c", confirm_sha, runs["customers"]),
                                             esha, label, do_confirm("customer"))
        resumed += ["confirm_customers"] if r else []
    return {"runs": runs, "resumed": resumed}


# ---------------------------------------------------------------- reconciliation
def reconcile(db: Database, ctx: Context, plan: Plan, runs: dict, ex: Export, archive_counts: dict | None) -> dict:
    out: dict = {"mismatches": []}
    mm = out["mismatches"]
    item_map = _map(db, runs["items"], "product_id")
    prog: dict[str, list] = defaultdict(lambda: [0, Decimal(0)])
    for r in db.all("SELECT item_id, qty_delta, unit_cost FROM stock_movements WHERE org_id=? AND ref_type='import_run'"
                    " AND ref_id=?", (ctx.org_id, runs["stock"])):
        prog[r["item_id"]][0] += r["qty_delta"]
        prog[r["item_id"]][1] += r["qty_delta"] * (Decimal(r["unit_cost"]) if r["unit_cost"] else 0)
    tot = {k: Decimal(0) for k in ("raw_units", "raw_cost", "planned_cost", "program_cost", "listed_cost")}
    tot["planned_units"] = tot["program_units"] = 0
    seen_items = set()
    for pid, t in plan.per_item.items():
        iid = item_map.get(pid)
        seen_items.add(iid)
        pu, pc = prog.get(iid, (0, Decimal(0))) if iid else (0, Decimal(0))
        for k in ("raw_units", "raw_cost", "planned_cost", "listed_cost"):
            tot[k] += t[k]
        tot["planned_units"] += t["planned_units"]
        tot["program_units"] += pu
        tot["program_cost"] += pc
        why = []
        if pu != t["planned_units"] or abs(pc - t["planned_cost"]) >= HALF_PIASTER:
            why.append(f"البرنامج {pu} وحدة / {egp(pc)} ج، المفروض {t['planned_units']} / {egp(t['planned_cost'])}")
        # the program keeps the cost per base unit to 6 decimals: at most half a millionth per unit can differ
        tol = HALF_PIASTER + t["planned_units"] * COST_Q / 2
        if abs(t["raw_units"] + t["listed_units"] - t["planned_units"]) > NOISE * 10 or \
                abs(t["raw_cost"] + t["listed_cost"] - t["planned_cost"]) >= tol:
            why.append(f"إي ستوك {t['raw_units'].normalize()} وحدة / {egp(t['raw_cost'])} ج والفرق مش متسجل في التقرير")
        if why:
            per = (t["planned_cost"] / t["planned_units"]) if t["planned_units"] else \
                (t["raw_cost"] / t["raw_units"] if t["raw_units"] else Decimal(0))
            value = abs(pc - t["planned_cost"]) + abs(t["raw_cost"] + t["listed_cost"] - t["planned_cost"]) + \
                (abs(pu - t["planned_units"]) + abs(t["raw_units"] + t["listed_units"] - t["planned_units"])) * abs(per)
            mm.append({"what": "stock", "code": t["code"], "name": t["name"], "detail": "؛ ".join(why), "value": value})
    for iid, (pu, pc) in prog.items():
        if iid not in seen_items:
            mm.append({"what": "stock", "code": iid, "name": "", "detail": f"رصيد في البرنامج مش في إي ستوك: {pu}",
                       "value": abs(pc)})
    out["stock"] = {"items_with_stock": len(plan.per_item), "rows_posted": len(plan.stock),
                    "estock_units": str(tot["raw_units"].normalize()), "estock_cost": egp(tot["raw_cost"]),
                    "planned_units": tot["planned_units"], "planned_cost": egp(tot["planned_cost"]),
                    "program_units": tot["program_units"], "program_cost": egp(tot["program_cost"]),
                    "cost_difference_listed_in_data_quality": egp(tot["listed_cost"]),
                    "cost_difference_from_unit_cost_rounding": egp(tot["planned_cost"] - tot["raw_cost"] - tot["listed_cost"])}
    if inventory.verify_balances(db, ctx.org_id):
        mm.append({"what": "stock", "code": "", "name": "", "detail": "أرصدة المخزن مش مطابقة لدفتر الحركات", "value": Decimal(0)})
    n_items = db.one("SELECT count(*) n FROM items WHERE org_id=?", (ctx.org_id,))["n"]
    if n_items != len(plan.items) or len(item_map) != len(plan.items):
        mm.append({"what": "items", "code": "", "name": "", "detail": f"الأصناف {n_items}، إي ستوك {len(plan.items)}", "value": Decimal(0)})
    out["items"] = {"estock": len(plan.items), "program": n_items}
    for kind, table, ltable, col, parties in (("suppliers", "suppliers", "supplier_ledger", "supplier_id", plan.suppliers),
                                             ("customers", "customers", "customer_ledger", "customer_id", plan.customers)):
        n = db.one(f"SELECT count(*) n FROM {table} WHERE org_id=?", (ctx.org_id,))["n"]
        bal = {r["k"]: r["b"] for r in db.all(f"SELECT {col} k, sum(amount) b FROM {ltable} WHERE org_id=? GROUP BY {col}",
                                               (ctx.org_id,))}
        m = _map(db, runs[kind], "estock_id")
        for d in parties:
            got = bal.get(m.get(d["estock_id"]), 0)
            if got != d["balance"]:
                mm.append({"what": kind, "code": d["code"], "name": d["name"],
                           "detail": f"الرصيد في البرنامج {egp(Decimal(got) / 100)}، المفروض {egp(Decimal(d['balance']) / 100)}",
                           "value": abs(Decimal(got - d["balance"]) / 100)})
        if n != len(parties):
            mm.append({"what": kind, "code": "", "name": "", "detail": f"العدد {n}، إي ستوك {len(parties)}", "value": Decimal(0)})
        out[kind] = {"estock": len(parties), "program": n,
                     "estock_balance_total": egp(Decimal(sum(d["estock_balance"] for d in parties)) / 100),
                     "confirmed_balance_total": egp(Decimal(sum(d["balance"] for d in parties)) / 100),
                     "opening_adjustment_total": egp(Decimal(sum(d["adjustment"] for d in parties)) / 100),
                     "program_balance_total": egp(Decimal(sum(bal.values())) / 100)}
    counts = {}
    for t in ex.tables.values():
        _, rows = ex.raw(t["name"])
        c = {"manifest": t["rows"], "csv": len(rows), "archive": (archive_counts or {}).get(t["name"])}
        counts[t["name"]] = c
        if (t["rows"] is not None and c["csv"] != t["rows"]) or (archive_counts is not None and c["archive"] != c["csv"]):
            mm.append({"what": "history", "code": t["name"], "name": "", "value": Decimal(0),
                       "detail": f"عدد الصفوف: التصدير {t['rows']}، الملف {c['csv']}، الأرشيف {c['archive']}"})
    out["history"] = {k: counts[ex.tables[k.lower()]["name"]] for k in HISTORY_TABLES if ex.has(k)}
    out["tables"] = len(counts)
    for b in plan.blocking:
        mm.append({"what": "data-quality", "code": b["code"], "name": b["field"], "detail": b["action"],
                   "value": abs(Decimal(b["value_egp"] or 0))})
    mm.sort(key=lambda x: x["value"], reverse=True)
    for x in mm:
        x["value"] = egp(x["value"])
    out["ok"] = not mm
    return out


# ---------------------------------------------------------------- reports
def write_reports(out_dir: Path, plan: Plan, rec: dict, extra: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"data_quality": out_dir / "data-quality.csv", "reconciliation": out_dir / "reconciliation.txt",
             "json": out_dir / "report.json", "openings": out_dir / "openings-to-confirm.xlsx",
             "supplier_contacts": out_dir / "supplier-contacts.csv"}
    order = {"block": 0, "review": 1, "fixed": 2, "info": 3}
    with open(paths["data_quality"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["الخطورة", "الجدول", "رقم إي ستوك", "الكود", "الخانة", "قيمة إي ستوك", "اللي اتعمل", "القيمة ج"])
        for d in sorted(plan.dq, key=lambda d: (order[d["severity"]], -abs(Decimal(d["value_egp"] or 0)))):
            w.writerow([{"block": "يوقف النقل", "review": "للمراجعة", "fixed": "اتصلح", "info": "للعلم"}[d["severity"]], d["table"],
                        d["estock_id"], d["code"], d["field"], d["estock_value"], d["action"], d["value_egp"]])
    with open(paths["supplier_contacts"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["رقم إي ستوك", "الكود", "المورد", "التليفون", "المندوبين والملاحظات"])
        for d in plan.suppliers:
            w.writerow([d["estock_id"], d["code"], d["name"], d.get("phone") or "", " | ".join(d.get("contacts") or [])])
    write_openings(plan, paths["openings"])
    lines = ["مطابقة نقل إي ستوك — " + ("OK" if rec["ok"] else "FAILED"), ""]
    sd = plan.stock_source.get("decision") or {}
    lines.append(f"جدول الرصيد الشغال: {sd.get('key', 'مش متحدد')} ({sd.get('why', '')})")
    for c in plan.stock_source.get("candidates", []):
        lines.append(f"  {c['key']}: {c['row_count']} سطر، {c['units_packs']} علبة، {c['cost']} ج، "
                     f"آخر تغيير {c['last_change']}، اتغيّر بعد البيع {c['fresh_hits']}/{c['checked']}")
    lines.append("المخازن: " + "، ".join(f"{s['id']} {s['name']}" + (" (اتنقل)" if s["id"] in plan.stock_source.get(
        "chosen_stores", []) else " (ما اتنقلش)") for s in plan.stock_source.get("stores", [])))
    for k, label in (("items", "الأصناف"), ("stock", "الرصيد"), ("suppliers", "الموردين"), ("customers", "العملاء")):
        if k in rec:
            lines.append(f"{label}: " + "، ".join(f"{a}={b}" for a, b in rec[k].items()))
    lines.append("التاريخ (عدد الصفوف): " + "، ".join(f"{k}={v['csv']}" for k, v in rec.get("history", {}).items()))
    lines.append(f"جودة البيانات: {len(plan.dq)} سطر ({len(plan.blocking)} يوقف النقل) في {paths['data_quality'].name}")
    lines.append("إشارة الأرصدة من كشف الحساب: " + json.dumps(plan.checks.get("balance_sign", {}), ensure_ascii=False))
    exp = [d for d in plan.dq if d["field"] == "expiry"]
    lines.append(f"من غير صلاحية: دواء أو مش معروف {sum(d['severity'] == 'review' for d in exp)} (للمراجعة)، "
                 f"مش دواء {sum(d['severity'] == 'info' for d in exp)} (عادي)")
    mi = plan.checks.get("money_info") or {}
    lines.append("خزنة إي ستوك والمحافظ — للعلم بس، مفيش رصيد خزنة ولا محفظة اتعمل من إي ستوك:")
    for t in mi.get("tables", []):
        lines.append(f"  {t['table']}: {t['rows']} سطر" + ("، " + "، ".join(f"{a}={b}" for a, b in t["sums"].items())
                                                         if t["sums"] else ""))
    for c in mi.get("last_shift_close", []):
        lines.append(f"  آخر قفل خزنة ({c['source']}، خزنة {c['cash_id']}، {c['at']}): البرنامج {c['system_cash']}، "
                     f"المعدود {c['counted_cash']}")
    for n in mi.get("sales_by_network", []):
        lines.append(f"  {n['source']} شبكة {n['network_id']}: {n['total']}")
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    if rec["mismatches"]:
        lines += ["", f"عدم مطابقة ({len(rec['mismatches'])})، الأكبر قيمة الأول:"]
        lines += [f"  [{m['what']}] {m['code']} {m['name']}: {m['detail']} ({m['value']} ج)" for m in rec["mismatches"]]
    paths["reconciliation"].write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    paths["json"].write_text(json.dumps({"reconciliation": rec, "stock_source": {k: v for k, v in plan.stock_source.items()},
                                         "checks": plan.checks, "rules": plan.rules, "extra": extra,
                                         "data_quality_rows": len(plan.dq)}, ensure_ascii=False, indent=1, default=str),
                             encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}


# ---------------------------------------------------------------- one run
def run(db: Database, ctx: Context, *, export_dir: Path, out_dir: Path, archive_path: Path, write_archive: bool,
        confirm_file: Path | None = None, packs_file: Path | None = None, stock_source: str | None = None,
        stores: list[str] | None = None, database: str | None = None) -> dict:
    """Plan, apply into `db` (the copy on a dry run), build the archive, reconcile, write the reports.
    `write_archive` False = the archive is built in a temp file only to count it (dry run)."""
    import tempfile
    from . import estock_archive
    from .estock_job import read_packs
    ex = Export(export_dir, database)
    packs = read_packs(Path(packs_file)) if packs_file else {}
    confirm = read_confirm(Path(confirm_file)) if confirm_file else {}
    plan = build_plan(ex, packs=packs, stock_source=stock_source, stores=stores, confirm=confirm)
    why = refusal(db, ctx, ex.source_sha())
    if why:
        raise Conflict(why, code="estock_db_refused")
    if write_archive and archive_path.exists() and estock_archive.source_sha(archive_path) != ex.source_sha():
        raise Conflict(f"فيه أرشيف إي ستوك من تصدير تاني: {archive_path}. مفيش أي حاجة اتكتبت", code="estock_db_refused")
    extra = {"export": f"{ex.instance}/{ex.database}", "export_sha": ex.source_sha()[:16],
             "confirm_file": str(confirm_file or ""), "packs_file": str(packs_file or "")}
    if plan.blocking:
        rec = {"ok": False, "mismatches": [], "history": {}}
        for b in plan.blocking:
            rec["mismatches"].append({"what": "data-quality", "code": b["code"], "name": b["field"],
                                      "detail": b["action"], "value": abs(Decimal(b["value_egp"] or 0))})
        rec["mismatches"].sort(key=lambda x: x["value"], reverse=True)
        for x in rec["mismatches"]:
            x["value"] = egp(x["value"])
        extra["applied"] = "لا: فيه مشاكل محتاجة قرار"
        return {"ok": False, "applied": False, "reports": write_reports(out_dir, plan, rec, extra), "reconciliation": rec,
                "plan": plan}
    sha = lambda f: hashlib.sha256(Path(f).read_bytes()).hexdigest() if f else ""
    res = apply_plan(db, ctx, plan, ex, packs_sha=sha(packs_file), confirm_sha=confirm.get("sha", ""))
    maps = {"items": _map(db, res["runs"]["items"], "product_id"),
            "customers": _map(db, res["runs"]["customers"], "estock_id"),
            "suppliers": _map(db, res["runs"]["suppliers"], "estock_id")}
    if write_archive:
        counts = estock_archive.build(ex, archive_path, maps)
        extra["archive"] = str(archive_path)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            counts = estock_archive.build(ex, Path(tmp) / estock_archive.NAME, maps, keep=False)
        extra["archive"] = "(تجربة: اتبنى واتعد ومش محفوظ)"
    if res["resumed"]:
        extra["resumed_steps"] = ", ".join(res["resumed"])
    rec = reconcile(db, ctx, plan, res["runs"], ex, counts)
    return {"ok": rec["ok"], "applied": True, "reports": write_reports(out_dir, plan, rec, extra),
            "reconciliation": rec, "plan": plan, "runs": res["runs"]}
