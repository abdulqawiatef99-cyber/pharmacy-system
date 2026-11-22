"""U4-1: what each item sold, month by month, from BOTH the E-Stock sales archive and the program's own sales.

The smart order counted only the program's sales (since the move, 21/11 18:04) and the manual monthly upload
(consumption_history), so right after the move it suggested almost nothing: the years of E-Stock sales sat
unread in the archive (estock_archive.py, read-only). Rules:
- archive lines: E-Stock's `amount` is in packs (fractions = part of a pack, as its stock table); base units =
  packs x the program item's pack size. The program item is found through the archive's map (the move's own
  result), else by the E-Stock product code = the program item code (the rule the move used);
- returns are netted: the line's own back_amount when E-Stock filled it on any line, else the return invoices
  (Back_Sales_details) in the month of the return; never both (no double count);
- one event per invoice: an item on one invoice is one sale, whatever the quantity;
- the cut-off: the archive counts only BEFORE the program's first sale, the program only from it, so a sale is
  never counted twice; the manual monthly upload counts only for months the archive does not cover.
Quantities are forecast figures for the order screen (never stock or money): month totals are rounded half up
to whole base units once, at the end.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from ..core.permissions import Context
from ..db import Database

_lock = threading.Lock()
_cache: dict[tuple, dict] = {}            # (archive path, mtime, org, branch, cut-off) -> archive months


def _dec(v) -> Decimal:
    try:
        d = Decimal(str(v).strip()) if v not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


def _month(at) -> str | None:
    s = str(at or "").strip()
    return s[:7] if len(s) >= 7 and s[4] == "-" else None


def _local(utc_iso: str) -> str:
    """The program stores UTC ('...Z'); E-Stock wrote the PC's local time. Compared as local text."""
    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def cutoff(db: Database, ctx: Context) -> str | None:
    """The program's first sale on this branch, local time: the archive counts before it, the program from it."""
    r = db.one("SELECT min(created_at) t FROM stock_movements WHERE org_id=? AND branch_id=? AND movement_type='sale'",
               (ctx.org_id, ctx.branch_id))
    return _local(r["t"]) if r and r["t"] else None


def _program_items(db: Database, org_id: str) -> tuple[dict[str, int], dict[str, str]]:
    pack, by_code = {}, {}
    for r in db.all("SELECT id, code, pack_size FROM items WHERE org_id=?", (org_id,)):
        pack[r["id"]] = max(int(r["pack_size"] or 1), 1)
        if r["code"]:
            by_code.setdefault(str(r["code"]).strip(), r["id"])
    return pack, by_code


def _archive_months(db: Database, ctx: Context, cut: str | None, lo: str | None = None) -> dict:
    """{"items": {item_id: {month: [packs Decimal, invoice ids set]}}, "first": first local day} or {} without archive."""
    from ..dataimport import estock_history as eh
    p = eh.archive_path(db)
    if not p:
        return {}
    from ..dataimport import estock_archive
    if estock_archive.owner_org(p) != ctx.org_id:
        return {}                                          # another pharmacy's archive: nothing (#345)
    key = (str(p), Path(p).stat().st_mtime, ctx.org_id, ctx.branch_id, cut, lo)
    with _lock:
        if key in _cache:
            return _cache[key]
    with closing(estock_archive.open_for(p, ctx.org_id)) as con:
        con.row_factory = sqlite3.Row
        a = eh._Arch(con)
        out = _read_archive(db, ctx, a, cut, lo)
    with _lock:
        if len(_cache) > 8:
            _cache.clear()                                 # a few windows of one archive: no growth
        _cache[key] = out
    return out


def _read_archive(db: Database, ctx: Context, a, cut: str | None, lo: str | None = None) -> dict:
    lt, extra, xp = a.lines()
    if not (a.has("Sales_header") and a.has(lt)):
        return {}
    emap = a.map("items")
    _, by_code = _program_items(db, ctx.org_id)
    codes: dict[str, str] = {}
    if a.has("Products"):
        for r in a.rows(f'SELECT {a.col("Products", "product_id")} pid, {a.col("Products", "product_code")} code'
                        f' FROM "e_Products"'):
            if r["pid"] is not None and r["code"]:
                codes[str(r["pid"]).strip()] = str(r["code"]).strip()

    def item_of(pid) -> str | None:
        pid = str(pid or "").strip()
        if pid.endswith(".0"):
            pid = pid[:-2]
        return emap.get(pid) or by_code.get(codes.get(pid, "\0"))

    at = f'replace({a.col("Sales_header", "insert_date", "h")}, " ", "T")'
    conds, params = [], []
    if cut:
        conds.append(f"{at} < ?")
        params.append(cut)
    if lo:
        conds.append(f"{at} >= ?")
        params.append(lo)
    where = " WHERE " + (" AND ".join(conds) or "1=1")
    rows = a.rows(f'SELECT {a.col(lt, "product_id", "l")} pid, {a.col("Sales_header", "sales_id", "h")} sid, {at} at,'
                  f' {a.col(lt, "amount", "l")} amt, {a.col(lt, "back_amount", "l")} back'
                  f' FROM "e_Sales_header" h JOIN "e_{lt}" l ON {a.col(lt, "sales_id", "l")}={a.col("Sales_header", "sales_id", "h")}'
                  f'{where}{extra}', (*params, *xp))
    line_backs = any(_dec(r["back"]) > 0 for r in rows)
    items: dict[str, dict[str, list]] = {}
    first = None
    for r in rows:
        m, iid = _month(r["at"]), item_of(r["pid"])
        if not m or not iid:
            continue
        first = min(first, r["at"][:10]) if first else r["at"][:10]
        net = _dec(r["amt"]) - (_dec(r["back"]) if line_backs else 0)
        cell = items.setdefault(iid, {}).setdefault(m, [Decimal(0), set()])
        cell[0] += net
        if net > 0:
            cell[1].add(str(r["sid"]))
    if not line_backs and a.has("Back_sales_header") and a.has("Back_Sales_details"):
        bat = f'replace({a.col("Back_sales_header", "insert_date", "h")}, " ", "T")'
        for r in a.rows(f'SELECT {a.col("Back_Sales_details", "product_id", "l")} pid, {bat} at,'
                        f' coalesce({a.col("Back_Sales_details", "back_amount", "l")}, {a.col("Back_Sales_details", "amount", "l")}) amt'
                        f' FROM "e_Back_sales_header" h JOIN "e_Back_Sales_details" l'
                        f' ON {a.col("Back_Sales_details", "back_sales_id", "l")}={a.col("Back_sales_header", "back_sales_id", "h")}'
                        + where.replace(at, bat), params):
            m, iid = _month(r["at"]), item_of(r["pid"])
            if m and iid:
                items.setdefault(iid, {}).setdefault(m, [Decimal(0), set()])[0] -= _dec(r["amt"])
    return {"items": items, "first": first}


def monthly(db: Database, ctx: Context, *, months: int = 12, today: date | None = None) -> dict:
    """Per item, the last `months` calendar months (this month included, oldest first):
    {"months": ["2025-12", ...], "items": {item_id: {month: {"units": int, "events": int}}}, "cutoff", "archive_from"}.
    Sources: archive before the cut-off, the program's own sales (net of returns) from it, the manual monthly
    upload only for months before the archive starts."""
    ctx.require("purchases.read")
    today = today or date.today()
    ms = []
    y, m = today.year, today.month
    for _ in range(months):
        ms.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    ms.reverse()
    keep = set(ms)
    pack, _ = _program_items(db, ctx.org_id)
    cut = cutoff(db, ctx)
    arch = _archive_months(db, ctx, cut, lo=f"{ms[0]}-01T00:00:00")
    acc: dict[str, dict[str, list]] = {}               # item -> month -> [units Decimal, events int]
    for iid, per in (arch.get("items") or {}).items():
        if iid not in pack:
            continue
        for mo, (packs, sids) in per.items():
            if mo in keep:
                c = acc.setdefault(iid, {}).setdefault(mo, [Decimal(0), 0])
                c[0] += packs * pack[iid]
                c[1] += len(sids)
    start_local = f"{ms[0]}-01T00:00:00"
    for r in db.all(
            "WITH w AS MATERIALIZED (SELECT item_id, ref_id, movement_type, qty_delta, created_at FROM stock_movements"
            " WHERE org_id=? AND +branch_id=? AND movement_type IN ('sale','sale_return') AND created_at >= ?)"
            " SELECT item_id, ref_id, movement_type, qty_delta, created_at FROM w",
            (ctx.org_id, ctx.branch_id, _utc(start_local))):
        mo = _local(r["created_at"])[:7]
        if mo not in keep:
            continue
        c = acc.setdefault(r["item_id"], {}).setdefault(mo, [Decimal(0), 0, set()])
        if len(c) == 2:
            c.append(set())
        c[0] -= int(r["qty_delta"])
        if r["movement_type"] == "sale" and r["ref_id"] not in c[2]:
            c[2].add(r["ref_id"])
            c[1] += 1
    first = arch.get("first")
    for r in db.all("SELECT item_id, period_start, period_end, qty_base FROM consumption_history WHERE org_id=?"
                    " AND branch_id=?", (ctx.org_id, ctx.branch_id)):
        mo = str(r["period_start"])[:7]
        if mo not in keep or str(r["period_end"])[:7] != mo:
            continue                                     # whole calendar months only (the upload is monthly)
        if first and str(r["period_end"]) >= first:
            continue                                     # the archive covers it: never both
        if cut and _local_day(cut) <= str(r["period_end"]):
            continue                                     # the program's own sales cover it
        c = acc.setdefault(r["item_id"], {}).setdefault(mo, [Decimal(0), 0])
        c[0] += Decimal(int(r["qty_base"]))
        c[1] += max(1, -(-int(r["qty_base"]) // pack.get(r["item_id"], 1))) if int(r["qty_base"]) > 0 else 0
    items = {iid: {mo: {"units": int(c[0].quantize(Decimal(1), ROUND_HALF_UP)), "events": int(c[1])}
                   for mo, c in per.items()} for iid, per in acc.items()}
    return {"months": ms, "items": items, "cutoff": cut, "archive_from": first}


def _local_day(local_iso: str) -> str:
    return local_iso[:10]


def _utc(local_iso: str) -> str:
    """Local wall time -> the program's UTC text, for comparing with created_at."""
    dt = datetime.fromisoformat(local_iso).astimezone().astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def archive_window(db: Database, ctx: Context, start_local: str) -> tuple[dict[str, tuple[int, int]], str | None]:
    """For the smart order's window: ({item_id: (base units net, invoices)}, first archived day in the window) of
    the archive from start_local (local time) up to the cut-off. ({}, None) without an archive."""
    arch = _archive_months(db, ctx, cutoff(db, ctx), lo=start_local)
    if not arch:
        return {}, None
    pack, _ = _program_items(db, ctx.org_id)
    out = {}
    for iid, per in arch["items"].items():
        if iid not in pack:
            continue
        packs = sum((c[0] for c in per.values()), Decimal(0))
        sids = set().union(*(c[1] for c in per.values()))
        out[iid] = (int((packs * pack[iid]).quantize(Decimal(1), ROUND_HALF_UP)), len(sids))
    return out, arch.get("first")
