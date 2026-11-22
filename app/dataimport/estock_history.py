"""E-Stock's history inside the program (owner 18/11 22:05): every old invoice, return, purchase, statement
line, shift close and shortage, searchable and viewable, each row marked `source: "estock"`.

Read only, from the archive next to the database (estock_archive.py, opened mode=ro). E-Stock's ids are
mapped to the program's items, customers and suppliers through the archive's map tables, so a customer's
or an item's history can be asked by its program id. Amounts are E-Stock's as they are, in EGP text (two
decimals), never mixed into the program's own totals. The API routes and screens come with the history
screens (after #336); every function checks the same permission as the program's own screen for it.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from . import estock_archive

SOURCE = "estock"
MAX_ROWS = 2000


def archive_path(db: Database) -> Path | None:
    if db.path == ":memory:":
        return None
    p = Path(db.path).parent / estock_archive.NAME
    return p if p.is_file() else None


def available(db: Database) -> bool:
    return archive_path(db) is not None


def _con(db: Database, ctx: Context) -> sqlite3.Connection:
    p = archive_path(db)
    if not p:
        raise NotFound("مفيش تاريخ إي ستوك في البرنامج ده (النقل ماتعملش)", code="no_estock_history")
    con = estock_archive.open_for(p, ctx.org_id)           # this pharmacy's archive only (#345 review)
    con.row_factory = sqlite3.Row
    return con


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v).strip()) if v not in (None, "") else None
    except InvalidOperation:
        return None
    return d if d is not None and d.is_finite() else None


def _egp(v) -> str | None:
    try:
        d = Decimal(str(v).strip()) if v not in (None, "") else None
    except InvalidOperation:
        return None
    return str(d.quantize(Decimal("0.01"), ROUND_HALF_UP)) if d is not None and d.is_finite() else None


def _txt(v) -> str | None:
    """E-Stock's char fields are padded with spaces: trimmed, and blank is None."""
    return (str(v).strip() or None) if v is not None else None


def _day(v) -> str:
    return str(v or "")[:10]


def _limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationFailed("عدد غير صحيح")
    return min(limit, MAX_ROWS)


class _Arch:
    """Column access that tolerates what an E-Stock version does not have (a missing column reads NULL)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._cols: dict[str, dict[str, str]] = {}

    def has(self, t: str) -> bool:
        return f"e_{t}" in self.tables

    def col(self, t: str, c: str, alias: str = "") -> str:
        cols = self._cols.setdefault(t, {r[1].lower(): r[1] for r in self.con.execute(f'PRAGMA table_info("e_{t}")')}
                                     if self.has(t) else {})
        return f'{alias + "." if alias else ""}"{cols[c]}"' if c in cols else "NULL"

    def lines(self) -> tuple[str, str, tuple]:
        """The local sales lines: Sales_details when it was read, else the Branches_ mirror of co_inf.branch_id
        only (the mirror also holds the other pharmacy's rows, with overlapping ids; #345 review).
        Returns (table, extra WHERE on alias l, params)."""
        # a local table emptied by quarantine is still the source, never the mirror (Codex on #385)
        if self.has("Sales_details") and (self.rows('SELECT 1 FROM "e_Sales_details" LIMIT 1') or (
                self.rows("SELECT 1 FROM sqlite_master WHERE name='q_Sales_details'")
                and self.rows('SELECT 1 FROM "q_Sales_details" LIMIT 1'))):
            return "Sales_details", "", ()
        b = None
        if self.has("co_inf"):
            r = self.rows(f'SELECT {self.col("co_inf", "branch_id")} b FROM "e_co_inf" LIMIT 1')
            b = str(r[0]["b"]).split(".")[0] if r and r[0]["b"] not in (None, "") else None
        if b is None:
            return "Branches_sales_details", "", ()
        return "Branches_sales_details", f' AND CAST({self.col("Branches_sales_details", "branch_id", "l")} AS INTEGER)=?', (int(b),)

    def map(self, kind: str) -> dict[str, str]:
        if f"map_{kind}" not in self.tables:
            return {}
        return {r[0]: r[1] for r in self.con.execute(f'SELECT estock_id, program_id FROM "map_{kind}"')}

    def rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.con.execute(sql, params).fetchall()


def _names(db: Database, ctx: Context, table: str, ids) -> dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    col = "coalesce(name_en, name_ar)" if table == "items" else "name"
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out.update({r["id"]: r["n"] for r in db.all(f"SELECT id, {col} n FROM {table} WHERE org_id=? AND id IN"
                                                    f" ({','.join('?' * len(part))})", (ctx.org_id, *part))})
    return out


def _program_to_estock(a: _Arch, kind: str, program_id: str) -> list[str]:
    return [e for e, p in a.map(kind).items() if p == program_id]


def _period(where: list, params: list, expr: str, date_from, date_to) -> None:
    for v, op in ((date_from, ">="), (date_to, "<=")):
        if v:
            try:
                ok = isinstance(v, str) and date.fromisoformat(v).isoformat() == v
            except ValueError:
                ok = False
            if not ok:
                raise ValidationFailed("التاريخ لازم يكون YYYY-MM-DD")
            where.append(f"substr({expr}, 1, 10) {op} ?")
            params.append(v)


# ---------------------------------------------------------------- invoices
_KINDS = {
    # kind: (header table, id col, lines table, party col, party map, permission, total col)
    "sale": ("Sales_header", "sales_id", "Branches_sales_details", "customer_id", "customers", "sales.totals",
             "total_bill_net"),
    "sale_return": ("Back_sales_header", "back_sales_id", "Back_Sales_details", "customer_id", "customers",
                    "sales.totals", "total_bill_net"),
    "purchase": ("Purchase_header", "purchase_id", "Purchase_details", "vendor_id", "suppliers", "purchases.read",
                 "total_bill"),
}


def invoices(db: Database, ctx: Context, kind: str = "sale", *, q: str | None = None, date_from: str | None = None,
             date_to: str | None = None, party_id: str | None = None, item_id: str | None = None,
             ticket: str | None = None, limit: int = 200) -> list[dict]:
    """Old E-Stock invoices (kind sale / sale_return / purchase), newest first. `q` = invoice or bill number;
    `party_id` = the program's customer (sales, returns) or supplier (purchases); `item_id` = invoices with that
    program item on a line; `ticket` = a sale whose ticket name, id or number contains it (spaces trimmed)."""
    if kind not in _KINDS:
        raise ValidationFailed("نوع الفاتورة غير معروف")
    head, idc, _, partyc, pmap, perm, totc = _KINDS[kind]
    ctx.require(perm)
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        if not a.has(head):
            return []
        where, params = ["1=1"], []
        if q:
            where.append(f"({a.col(head, idc)}=? OR trim({a.col(head, 'bill_number')})=?)")
            params += [str(q).strip(), str(q).strip()]
        _period(where, params, a.col(head, "insert_date"), date_from, date_to)
        if item_id:
            pids = _program_to_estock(a, "items", item_id)
            if not pids:
                return []
            lt, extra, xp = a.lines() if kind == "sale" else (_KINDS[kind][2], "", ())
            if not a.has(lt):
                return []
            where.append(f'{a.col(head, idc)} IN (SELECT {a.col(lt, idc, "l")} FROM "e_{lt}" l WHERE'
                         f' {a.col(lt, "product_id", "l")} IN ({",".join("?" * len(pids))}){extra})')
            params += [*pids, *xp]
        if ticket:
            if kind != "sale":
                return []
            t = f"%{str(ticket).strip()}%"
            where.append(f"(trim({a.col(head, 'ticket_num')}) LIKE ? OR trim({a.col(head, 'ticket_id')}) LIKE ?"
                         f" OR trim({a.col(head, 'ticket_name')}) LIKE ?)")
            params += [t, t, t]
        if party_id:
            ids = _program_to_estock(a, pmap, party_id)
            if not ids:
                return []
            where.append(f"{a.col(head, partyc)} IN ({','.join('?' * len(ids))})")
            params += ids
        rows = a.rows(f'SELECT {a.col(head, idc)} id, {a.col(head, "bill_number")} bill, {a.col(head, "insert_date")} at,'
                      f' {a.col(head, partyc)} party, {a.col(head, totc)} total, {a.col(head, "bill_cash")} cash,'
                      f' {a.col(head, "network_money")} network, {a.col(head, "ticket_name")} tname,'
                      f' {a.col(head, "ticket_id")} tref, {a.col(head, "ticket_num")} tno, {a.col(head, "sales_id")} sale'
                      f' FROM "e_{head}" WHERE {" AND ".join(where)} ORDER BY {a.col(head, "insert_date")} DESC LIMIT ?',
                      (*params, _limit(limit)))
        m = a.map(pmap)
    names = _names(db, ctx, pmap, [m.get(r["party"]) for r in rows])
    return [{"source": SOURCE, "kind": kind, "estock_id": r["id"], "bill_number": _txt(r["bill"]), "day": _day(r["at"]),
             "at": r["at"], "party_id": m.get(r["party"]), "party": names.get(m.get(r["party"])),
             "estock_party_id": r["party"], "total": _egp(r["total"]), "cash": _egp(r["cash"]),
             "network": _egp(r["network"]), "ticket_name": _txt(r["tname"]), "ticket_ref": _txt(r["tref"]),
             "ticket_no": _txt(r["tno"]),
             **({"sale_id": r["sale"]} if kind == "sale_return" else {})} for r in rows]


def invoice(db: Database, ctx: Context, kind: str, estock_id: str) -> dict:
    """One old invoice with its lines (items mapped to the program's items; cost only with costs.read)."""
    if kind not in _KINDS:
        raise ValidationFailed("نوع الفاتورة غير معروف")
    head_t, idc, lines_t, *_ = _KINDS[kind]
    found = [h for h in invoices(db, ctx, kind, q=str(estock_id), limit=50) if h["estock_id"] == str(estock_id)]
    if not found:
        raise NotFound("الفاتورة دي مش في تاريخ إي ستوك")
    show_cost = ctx.can("costs.read")
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        items = a.map("items")
        extra, xp = "", ()
        if kind == "sale":
            lines_t, extra, xp = a.lines()
        rows = a.rows(f'SELECT {a.col(lines_t, "product_id", "l")} pid, {a.col(lines_t, "amount", "l")} amount,'
                      f' {a.col(lines_t, "back_amount", "l")} back, {a.col(lines_t, "bouns", "l")} bonus,'
                      f' {a.col(lines_t, "sell_price", "l")} sp, {a.col(lines_t, "back_price", "l")} bp,'
                      f' {a.col(lines_t, "buy_price", "l")} cost, {a.col(lines_t, "total_sell", "l")} total,'
                      f' {a.col(lines_t, "disc_money", "l")} disc, {a.col(lines_t, "exp_date", "l")} exp'
                      f' FROM "e_{lines_t}" l WHERE {a.col(lines_t, idc, "l")}=?{extra}',
                      (str(estock_id), *xp)) if a.has(lines_t) else []
    names = _names(db, ctx, "items", [items.get(r["pid"]) for r in rows])
    out = dict(found[0])
    out["lines"] = [{"source": SOURCE, "item_id": items.get(r["pid"]), "item": names.get(items.get(r["pid"])),
                     "estock_product_id": r["pid"], "qty": r["amount"], "returned": r["back"], "bonus": r["bonus"],
                     "price": _egp(r["sp"]), "return_price": _egp(r["bp"]), "total": _egp(r["total"]),
                     "discount": _egp(r["disc"]), "expiry": _day(r["exp"]) or None,
                     **({"cost": _egp(r["cost"])} if show_cost else {})} for r in rows]
    return out


# ---------------------------------------------------------------- per customer / per item
def customer_items(db: Database, ctx: Context, customer_id: str) -> list[dict]:
    """What a customer bought in E-Stock, per item: quantity (E-Stock units, net of what came back), how many
    invoices, first and last day. Most bought first."""
    ctx.require("sales.read")
    if not db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id)):
        raise NotFound("العميل غير موجود")
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        ids = _program_to_estock(a, "customers", customer_id)
        lt, extra, xp = a.lines()
        if not ids or not (a.has("Sales_header") and a.has(lt)):
            return []
        rows = a.rows(f'SELECT {a.col(lt, "product_id", "l")} pid,'
                      f' sum(coalesce({a.col(lt, "amount", "l")}, 0) - coalesce({a.col(lt, "back_amount", "l")}, 0)) qty,'
                      f' count(DISTINCT {a.col("Sales_header", "sales_id", "h")}) n,'
                      f' min({a.col("Sales_header", "insert_date", "h")}) first, max({a.col("Sales_header", "insert_date", "h")}) last'
                      f' FROM "e_Sales_header" h JOIN "e_{lt}" l'
                      f' ON {a.col(lt, "sales_id", "l")}={a.col("Sales_header", "sales_id", "h")}'
                      f' WHERE {a.col("Sales_header", "customer_id", "h")} IN ({",".join("?" * len(ids))}){extra}'
                      f' GROUP BY pid ORDER BY qty DESC', (*ids, *xp))
        items = a.map("items")
    names = _names(db, ctx, "items", [items.get(r["pid"]) for r in rows])
    return [{"source": SOURCE, "item_id": items.get(r["pid"]), "item": names.get(items.get(r["pid"])),
             "estock_product_id": r["pid"], "qty": str(Decimal(str(r["qty"] or 0)).normalize()), "invoices": r["n"],
             "first_day": _day(r["first"]), "last_day": _day(r["last"])} for r in rows]


def item_history(db: Database, ctx: Context, item_id: str, *, limit: int = 500) -> list[dict]:
    """An item's E-Stock movements, newest first: sales lines, sale returns, purchase lines (cost only with
    costs.read), each with its invoice and day."""
    ctx.require("items.read")
    if not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
        raise NotFound("الصنف غير موجود")
    show_cost = ctx.can("costs.read")
    out: list[dict] = []
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        pids = _program_to_estock(a, "items", item_id)
        if not pids:
            return []
        ph = ",".join("?" * len(pids))
        lt, sale_extra, sale_xp = a.lines()
        for kind, head, idc, lines, qty, price, party, pmap in (
                ("sale", "Sales_header", "sales_id", lt, "amount", "sell_price", "customer_id", "customers"),
                ("sale_return", "Back_sales_header", "back_sales_id", "Back_Sales_details", "back_amount", "back_price",
                 "customer_id", "customers"),
                ("purchase", "Purchase_header", "purchase_id", "Purchase_details", "amount", "buy_price", "vendor_id",
                 "suppliers")):
            if not (a.has(head) and a.has(lines)):
                continue
            if kind == "purchase" and not ctx.can("purchases.read"):
                continue
            m = a.map(pmap)
            for r in a.rows(f'SELECT {a.col(head, idc, "h")} id, {a.col(head, "bill_number", "h")} bill,'
                            f' {a.col(head, "insert_date", "h")} at, {a.col(head, party, "h")} party,'
                            f' {a.col(lines, qty, "l")} qty, {a.col(lines, price, "l")} price, {a.col(lines, "buy_price", "l")} cost,'
                            f' {a.col(lines, "exp_date", "l")} exp'
                            f' FROM "e_{head}" h JOIN "e_{lines}" l ON {a.col(lines, idc, "l")}={a.col(head, idc, "h")}'
                            f' WHERE {a.col(lines, "product_id", "l")} IN ({ph}){sale_extra if kind == "sale" else ""}',
                            (*pids, *(sale_xp if kind == "sale" else ()))):
                row = {"source": SOURCE, "kind": kind, "estock_id": r["id"], "bill_number": r["bill"], "day": _day(r["at"]),
                       "at": r["at"], "party_id": m.get(r["party"]), "qty": r["qty"], "price": _egp(r["price"]),
                       "expiry": _day(r["exp"]) or None}
                if show_cost:
                    row["cost"] = _egp(r["cost"])
                out.append(row)
    out.sort(key=lambda r: r["at"] or "", reverse=True)
    out = out[:_limit(limit)]
    for kind, table in (("customers", "customers"), ("suppliers", "suppliers")):
        names = _names(db, ctx, table, [r["party_id"] for r in out])
        for r in out:
            if r["party_id"] in names:
                r["party"] = names[r["party_id"]]
    return out


# ---------------------------------------------------------------- statements, shifts, shortages, periods
def statement(db: Database, ctx: Context, kind: str, party_id: str) -> list[dict]:
    """The E-Stock account statement of a customer (Gedo_customers) or supplier (Gedo_Vendors), oldest first,
    as E-Stock holds it: for him / for me / running total / type / notes."""
    if kind == "customer":
        ctx.require("sales.read")
        table, pfx, idc, pmap, ptable = "Gedo_customers", "gc", "customer_id", "customers", "customers"
    elif kind == "supplier":
        ctx.require("suppliers.read")
        table, pfx, idc, pmap, ptable = "Gedo_Vendors", "gv", "vendor_id", "suppliers", "suppliers"
    else:
        raise ValidationFailed("النوع لازم يكون customer أو supplier")
    if not db.one(f"SELECT 1 FROM {ptable} WHERE id=? AND org_id=?", (party_id, ctx.org_id)):
        raise NotFound("غير موجود")
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        ids = _program_to_estock(a, pmap, party_id)
        if not ids or not a.has(table):
            return []
        rows = a.rows(f'SELECT {a.col(table, pfx + "_id")} id, {a.col(table, "insert_date")} at, {a.col(table, pfx + "_type")} type,'
                      f' {a.col(table, pfx + "_for_him")} for_him, {a.col(table, pfx + "_for_me")} for_me,'
                      f' {a.col(table, "total")} total, {a.col(table, "notes")} notes, {a.col(table, "gf_id")} gf'
                      f' FROM "e_{table}" WHERE {a.col(table, idc)} IN ({",".join("?" * len(ids))})'
                      f' ORDER BY {a.col(table, "insert_date")}, {a.col(table, pfx + "_id")}', tuple(ids))
    return [{"source": SOURCE, "estock_id": r["id"], "day": _day(r["at"]), "at": r["at"], "type": r["type"],
             "for_him": _egp(r["for_him"]), "for_me": _egp(r["for_me"]), "total": _egp(r["total"]), "notes": r["notes"],
             "estock_gf_id": r["gf"]} for r in rows]


def statement_with_opening(db: Database, ctx: Context, kind: str, party_id: str) -> dict:
    """The old statement as E-Stock holds it, with its last running total, next to the program's opening line
    (what the move posted, and any confirmed adjustment), so the two can be read side by side."""
    lines = statement(db, ctx, kind, party_id)
    ledger, col = ("customer_ledger", "customer_id") if kind == "customer" else ("supplier_ledger", "supplier_id")
    opening = db.all(f"SELECT amount, kind, note, created_at FROM {ledger} WHERE org_id=? AND {col}=? AND"
                     " (note LIKE '%رصيد افتتاحي%' OR note LIKE '%تسوية افتتاحية%') ORDER BY created_at",
                     (ctx.org_id, party_id))
    return {"source": SOURCE, "lines": lines, "estock_last_total": lines[-1]["total"] if lines else None,
            "program_opening": [{"amount": r["amount"], "kind": r["kind"], "note": r["note"], "at": r["created_at"]}
                                for r in opening],
            "program_opening_total": sum(r["amount"] for r in opening)}


def shift_closes(db: Database, ctx: Context, *, date_from: str | None = None, date_to: str | None = None,
                 limit: int = 500) -> list[dict]:
    ctx.require("sales.totals")
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        if not a.has("Cash_disk_close"):
            return []
        t = "Cash_disk_close"
        where, params = ["1=1"], []
        _period(where, params, a.col(t, "insert_date"), date_from, date_to)
        rows = a.rows(f'SELECT {a.col(t, "cdc_id")} id, {a.col(t, "insert_date")} at, {a.col(t, "cdc_cash_id")} cash,'
                      f' {a.col(t, "cdc_emp_id")} emp, {a.col(t, "cdc_shift_start_time")} start, {a.col(t, "cdc_start_cash")} s,'
                      f' {a.col(t, "cdc_curr_cash")} c, {a.col(t, "cdc_act_cash")} act, {a.col(t, "cdc_trans_value")} trans,'
                      f' {a.col(t, "cdc_fcs_id")} fcs, {a.col(t, "cdc_notice")} note FROM "e_{t}" WHERE {" AND ".join(where)}'
                      f' ORDER BY {a.col(t, "insert_date")} DESC LIMIT ?', (*params, _limit(limit)))
    return [{"source": SOURCE, "estock_id": r["id"], "day": _day(r["at"]), "at": r["at"], "cash_id": r["cash"],
             "employee": r["emp"], "shift_start": r["start"], "start_cash": _egp(r["s"]), "system_cash": _egp(r["c"]),
             "counted_cash": _egp(r["act"]), "transferred": _egp(r["trans"]), "to_treasury": r["fcs"], "note": r["note"]}
            for r in rows]


def shortages(db: Database, ctx: Context, *, date_from: str | None = None, date_to: str | None = None,
              limit: int = 500) -> list[dict]:
    ctx.require("items.read")
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        if not a.has("Shortcoming"):
            return []
        t = "Shortcoming"
        where, params = ["1=1"], []
        _period(where, params, a.col(t, "insert_date"), date_from, date_to)
        rows = a.rows(f'SELECT {a.col(t, "id")} id, {a.col(t, "insert_date")} at, {a.col(t, "product_id")} pid,'
                      f' {a.col(t, "amount")} qty, {a.col(t, "notes")} notes, {a.col(t, "class")} class'
                      f' FROM "e_{t}" WHERE {" AND ".join(where)} ORDER BY {a.col(t, "insert_date")} DESC LIMIT ?',
                      (*params, _limit(limit)))
        items = a.map("items")
    names = _names(db, ctx, "items", [items.get(r["pid"]) for r in rows])
    return [{"source": SOURCE, "estock_id": r["id"], "day": _day(r["at"]), "at": r["at"], "item_id": items.get(r["pid"]),
             "item": names.get(items.get(r["pid"])), "estock_product_id": r["pid"], "qty": r["qty"], "notes": r["notes"],
             "class": r["class"]} for r in rows]


def period_report(db: Database, ctx: Context, date_from: str, date_to: str, *, by: str = "month",
                  _total: dict | None = None) -> list[dict]:
    """E-Stock's totals per day or month: sales (net bill), sale returns, purchases (purchases only with
    purchases.read). E-Stock's own figures, apart from the program's reports."""
    ctx.require("sales.totals")
    if by not in ("day", "month"):
        raise ValidationFailed("التجميع لازم يكون day أو month")
    n = 10 if by == "day" else 7
    out: dict[str, dict] = {}
    with closing(_con(db, ctx)) as con:
        a = _Arch(con)
        for key, head, tot, perm in (("sales", "Sales_header", "total_bill_net", "sales.totals"),
                                     ("returns", "Back_sales_header", "total_bill_net", "sales.totals"),
                                     ("purchases", "Purchase_header", "total_bill", "purchases.read")):
            if not a.has(head) or not ctx.can(perm):
                continue
            where, params = ["1=1"], []
            _period(where, params, a.col(head, "insert_date"), date_from, date_to)
            for r in a.rows(f'SELECT substr({a.col(head, "insert_date")}, 1, {n}) p, {a.col(head, tot)} v'
                            f' FROM "e_{head}" WHERE {" AND ".join(where)}', tuple(params)):
                d = out.setdefault(r["p"], {"source": SOURCE, "period": r["p"]})
                d[key + "_count"] = d.get(key + "_count", 0) + 1
                v = _dec(r["v"])
                if v is None:                       # shown as is: counted apart, never a silent zero (U4 review)
                    d[key + "_unreadable"] = d.get(key + "_unreadable", 0) + 1
                d[key] = d.get(key, Decimal(0)) + (v or 0)                    # exact; rounded once below
    rows = [out[k] for k in sorted(out)]
    if _total is not None:                    # the period total from the exact values, rounded once (Codex P1 on #436)
        _total["source"] = SOURCE
        for d in rows:
            for k in ("sales", "returns", "purchases"):
                if k in d:
                    _total[k] = _total.get(k, Decimal(0)) + d[k]
                    for x in ("_count", "_unreadable"):
                        _total[k + x] = _total.get(k + x, 0) + d.get(k + x, 0)
        for k in ("sales", "returns", "purchases"):
            if k in _total:
                _total[k] = str(_total[k].quantize(Decimal("0.01"), ROUND_HALF_UP))
    for d in rows:
        for k in ("sales", "returns", "purchases"):
            if k in d:
                d[k] = str(d[k].quantize(Decimal("0.01"), ROUND_HALF_UP))
    return rows


def period_with_total(db: Database, ctx: Context, date_from: str, date_to: str, *, by: str = "month") -> tuple[list, dict]:
    """period_report's rows plus the whole period's totals, added from the exact values and rounded once."""
    total: dict = {}
    rows = period_report(db, ctx, date_from, date_to, by=by, _total=total)
    return rows, total
