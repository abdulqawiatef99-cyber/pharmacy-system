"""Contracts: a company / union / club whose members get an agreed discount — one rate for
local items and another for imported ones. The owner sets them and links customers; the
cashier only picks the customer, and the discount is applied by itself (still never below
cost without the owner)."""
from __future__ import annotations

from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

MAX_BP = 5000


def listing(db: Database, ctx: Context) -> list[dict]:
    ctx.require("sales.read")
    return [dict(r) for r in db.all(
        "SELECT c.*, (SELECT count(*) FROM customers x WHERE x.contract_id=c.id) customers FROM contracts c"
        " WHERE c.org_id=? ORDER BY c.is_active DESC, c.name", (ctx.org_id,))]


def save(db: Database, ctx: Context, data: dict, cid: str | None = None) -> list[dict]:
    ctx.require("contracts.manage")
    cur = None
    if cid:
        cur = db.one("SELECT * FROM contracts WHERE id=? AND org_id=?", (cid, ctx.org_id))
        if not cur:
            raise NotFound("التعاقد غير موجود")
    v = dict(cur) if cur else {"discount_local_bp": 0, "discount_imported_bp": 0, "note": None, "is_active": 1}
    for k in ("name", "discount_local_bp", "discount_imported_bp", "note", "is_active"):
        if k in data:
            v[k] = data[k]
    v["name"] = str(v.get("name") or "").strip()[:80]
    if len(v["name"]) < 2:
        raise ValidationFailed("اكتب اسم الجهة (شركة، نقابة، نادٍ…)")
    for k in ("discount_local_bp", "discount_imported_bp"):
        if isinstance(v[k], bool) or not isinstance(v[k], int) or not 0 <= v[k] <= MAX_BP:   # true is not 1 bp
            raise ValidationFailed("الخصم من 0 إلى 50%")
    v["note"] = (str(v.get("note") or "").strip()[:200]) or None
    v["is_active"] = int(bool(v["is_active"]))
    cols = ("name", "discount_local_bp", "discount_imported_bp", "note", "is_active")
    with db.transaction():
        try:
            if cur:
                db.execute(f"UPDATE contracts SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*[v[k] for k in cols], cid))
                audit.record(db, ctx, "contract.update", "contract", cid, before={k: cur[k] for k in cols}, after={k: v[k] for k in cols})
            else:
                cid = new_id()
                db.execute(f"INSERT INTO contracts (id, org_id, created_by, {', '.join(cols)}) VALUES (?,?,?{',?' * len(cols)})",
                           (cid, ctx.org_id, ctx.user_id, *[v[k] for k in cols]))
                audit.record(db, ctx, "contract.create", "contract", cid, after={k: v[k] for k in cols})
        except Exception as e:
            if "UNIQUE" in str(e):
                raise Conflict("يوجد تعاقد بالاسم نفسه") from e
            raise
    return listing(db, ctx)


def link_customer(db: Database, ctx: Context, customer_id: str, contract_id: str | None) -> None:
    ctx.require("contracts.manage")
    c = db.one("SELECT contract_id FROM customers WHERE id=? AND org_id=?", (customer_id, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    if contract_id is not None and not isinstance(contract_id, str):
        raise ValidationFailed("اختر التعاقد")
    if contract_id and not db.one("SELECT 1 FROM contracts WHERE id=? AND org_id=?", (contract_id, ctx.org_id)):
        raise NotFound("التعاقد غير موجود")
    with db.transaction():
        db.execute("UPDATE customers SET contract_id=? WHERE id=?", (contract_id, customer_id))
        audit.record(db, ctx, "customer.contract", "customer", customer_id, before={"contract": c["contract_id"]},
                     after={"contract": contract_id})


def apply(db: Database, sid: str) -> None:
    """Sets the contract of the sale's customer on its lines (inside the caller's transaction).
    Lines with a discount someone typed by hand are left alone."""
    s = db.one("SELECT customer_id FROM sales WHERE id=?", (sid,))
    k = db.one("SELECT k.* FROM customers c JOIN contracts k ON k.id=c.contract_id AND k.is_active=1 WHERE c.id=?",
               (s["customer_id"],)) if s and s["customer_id"] else None
    db.execute("UPDATE sales SET contract_id=? WHERE id=?", (k["id"] if k else None, sid))
    for l in db.all("SELECT l.id, l.discount_bp, l.discount_source, i.is_imported FROM sale_lines l JOIN items i ON i.id=l.item_id"
                    " WHERE l.sale_id=?", (sid,)):
        if l["discount_bp"] and l["discount_source"] != "contract":
            continue
        bp = (k["discount_imported_bp"] if l["is_imported"] else k["discount_local_bp"]) if k else 0
        db.execute("UPDATE sale_lines SET discount_bp=?, discount_source=?, discount_by=NULL, discount_approved_by=NULL WHERE id=?",
                   (bp, "contract" if bp else None, l["id"]))


TICKET_FIELDS = {"name": ("ticket_name", 80), "ref": ("ticket_ref", 40), "number": ("ticket_no", 40)}


def set_ticket(db: Database, ctx: Context, sid: str, data: dict) -> dict:
    """The ticket a contract sale is made on (hospital / company: name, id, number), all optional.
    On an open invoice by the cashier; on a completed one only by who manages contracts (a correction,
    audited). Only on a sale that carries a contract."""
    ctx.require("sales.create")
    if not isinstance(data, dict):
        raise ValidationFailed("بيانات التذكرة غير صحيحة")
    vals = {}
    for key, (col, size) in TICKET_FIELDS.items():
        v = data.get(key)
        if isinstance(v, bool) or (v is not None and not isinstance(v, (str, int))):
            raise ValidationFailed("بيانات التذكرة غير صحيحة")
        vals[col] = (str(v).strip()[:size] if v is not None else "") or None
    with db.transaction():
        s = db.one("SELECT id, status, contract_id, created_by, ticket_name, ticket_ref, ticket_no FROM sales"
                   " WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not s:
            raise NotFound("الفاتورة غير موجودة")
        if s["status"] == "voided":
            raise Conflict("الفاتورة ملغاة")
        if s["status"] == "completed":
            ctx.require("contracts.manage")
        else:     # an open invoice is changed by whoever may open it, as every other draft change (#340 review)
            from .service import Sales
            Sales._may_view(ctx, dict(s))
        if not s["contract_id"] and any(vals.values()):
            raise ValidationFailed("التذكرة لفواتير التعاقد بس: اختار عميل على تعاقد الأول", code="no_contract")
        before = {c: s[c] for c in vals}
        if before != vals:
            db.execute("UPDATE sales SET ticket_name=?, ticket_ref=?, ticket_no=? WHERE id=? AND org_id=?",
                       (vals["ticket_name"], vals["ticket_ref"], vals["ticket_no"], sid, ctx.org_id))
            audit.record(db, ctx, "sale.ticket", "sale", sid, before=before, after=vals)
    return vals


def _archive(db: Database, ctx: Context):
    """This pharmacy's E-Stock archive, read only, or None: through estock_archive.open_for, so another
    pharmacy in the same database reads nothing from it (#345 review)."""
    from ..core.errors import NotFound
    from ..dataimport import estock_history as eh
    if not eh.available(db):
        return None
    try:
        return eh._con(db, ctx)
    except NotFound:
        return None


def estock_history(db: Database, ctx: Context, contract_id: str, d1: date, d2: date) -> list[dict]:
    """E-Stock invoices of this contract's customers in the period, with their tickets, from the read-only
    archive next to the database (estock_archive.py). Empty when there is no archive. Never mixed into
    the program's totals: E-Stock history is shown apart."""
    customers = {r["id"]: r["name"] for r in db.all("SELECT id, name FROM customers WHERE org_id=? AND contract_id=?",
                                                    (ctx.org_id, contract_id))}
    con = _archive(db, ctx) if customers else None
    if con is None:
        return []
    with closing(con):
        have = {r[1].lower() for r in con.execute('PRAGMA table_info("e_Sales_header")')}
        if not {"sales_id", "customer_id", "insert_date"} <= have or not con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='map_customers'").fetchone():
            return []
        pick = lambda c: c if c in have else "NULL"   # noqa: E731 - a column E-Stock may not have
        ids = {r["estock_id"]: r["program_id"] for r in con.execute("SELECT estock_id, program_id FROM map_customers")
               if r["program_id"] in customers}
        if not ids:
            return []
        out = []
        q = (f'SELECT sales_id, customer_id, insert_date, {pick("bill_number")} bill_number, {pick("total_bill_net")} total,'
             f' {pick("ticket_name")} ticket_name, {pick("ticket_id")} ticket_ref, {pick("ticket_num")} ticket_no'
             f' FROM "e_Sales_header" WHERE substr(insert_date, 1, 10) BETWEEN ? AND ? AND customer_id IN'
             f' ({",".join("?" * len(ids))}) ORDER BY insert_date')
        for r in con.execute(q, (d1.isoformat(), d2.isoformat(), *ids)):
            try:
                total = int((Decimal(r["total"] or "0") * 100).quantize(Decimal(1), ROUND_HALF_UP))
            except InvalidOperation:
                total = None
            out.append({"estock_sales_id": r["sales_id"], "bill_number": r["bill_number"], "day": (r["insert_date"] or "")[:10],
                        "customer": customers[ids[r["customer_id"]]], "total": total,
                        "ticket_name": r["ticket_name"], "ticket_ref": r["ticket_ref"], "ticket_no": r["ticket_no"]})
        return out


def report(db: Database, ctx: Context, contract_id: str, date_from: str, date_to: str) -> dict:
    """Invoices of a contract's customers in a period (for settling with the company), with their tickets;
    `estock` = the same customers' invoices from E-Stock's archive, apart from the program's totals."""
    from .service import local_day_bounds
    ctx.require("sales.totals")
    k = db.one("SELECT * FROM contracts WHERE id=? AND org_id=?", (contract_id, ctx.org_id))
    if not k:
        raise NotFound("التعاقد غير موجود")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    rows = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.completed_at, s.total, s.discount_total, s.returned_total, s.on_credit, c.name customer,"
        " s.ticket_name, s.ticket_ref, s.ticket_no"
        " FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.org_id=? AND s.contract_id=? AND s.status='completed'"
        " AND s.completed_at>=? AND s.completed_at<? ORDER BY s.completed_at",
        (ctx.org_id, contract_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))]
    keys = ("total", "discount_total", "returned_total", "on_credit")
    return {"contract": dict(k), "from": d1.isoformat(), "to": d2.isoformat(), "rows": rows,
            "totals": {x: sum(r[x] or 0 for r in rows) for x in keys},
            "estock": estock_history(db, ctx, contract_id, d1, d2)}


def _egp(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.01"), ROUND_HALF_UP))


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v).strip()) if v not in (None, "") else None
    except InvalidOperation:
        return None
    return d if d is not None and d.is_finite() else None


def claim(db: Database, ctx: Context, contract_id: str, date_from: str, date_to: str) -> dict:
    """One claim for a period that spans E-Stock and the program (owner 18/11: Hikma, November = E-Stock
    from the 1st to the launch + the program to the 30th): every invoice line of the contract's customers,
    net of what came back, with its ticket, store, sale value and (with costs.read) its cost, from both
    sources, in one list, plus totals per customer and store. The claim's own rule (cost + half the margin
    for the pharmacy store, half the margin for the hospital's store) is applied by whoever makes the claim
    from these figures; this only gathers them, each line marked with its source.

    E-Stock line values come from its archive: sale = total_sell (else amount x sell_price - disc_money)
    minus back_amount x back_price; cost = (amount - back_amount) x buy_price. To be checked once against
    E-Stock's own «مبيعات أصناف عميل» report in the rehearsal before a real claim."""
    from .service import local_day_bounds, outstanding_value, returns_history
    ctx.require("sales.totals")
    show_cost = ctx.can("costs.read")
    k = db.one("SELECT * FROM contracts WHERE id=? AND org_id=?", (contract_id, ctx.org_id))
    if not k:
        raise NotFound("التعاقد غير موجود")
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("اختر الفترة")
    lines: list[dict] = []
    # the program: completed sales of this contract, each line at what the customer kept (outstanding_value)
    sales = {r["id"]: dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.completed_at, s.ticket_name, s.ticket_ref, s.ticket_no, c.id customer_id, c.name customer"
        " FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.org_id=? AND s.contract_id=?"
        " AND s.status='completed' AND s.completed_at>=? AND s.completed_at<?",
        (ctx.org_id, contract_id, local_day_bounds(d1)[0], local_day_bounds(d2)[1]))}
    history = returns_history(db, ctx.org_id, list(sales)) if sales else {}
    for l in db.all("SELECT l.id, l.sale_id, l.discount_bp, l.line_total, i.code, coalesce(i.name_en, i.name_ar) name"
                    " FROM sale_lines l JOIN items i ON i.id=l.item_id WHERE l.org_id=? AND l.sale_id IN"
                    f" ({','.join('?' * len(sales)) or 'NULL'}) ORDER BY l.sale_id, l.line_no", (ctx.org_id, *sales)):
        allocs = [dict(a) for a in db.all(       # scoped through the parent line (allocations have no org_id)
            "SELECT a.id, a.rowid rid, a.qty_base, a.returned_qty, a.unit_price, a.unit_cost, a.pack_price, a.pack_size"
            " FROM sale_allocations a JOIN sale_lines pl ON pl.id=a.sale_line_id"
            " WHERE a.sale_line_id=? AND pl.org_id=?", (l["id"], ctx.org_id))]
        kept = {a["id"]: a["qty_base"] - a["returned_qty"] for a in allocs}
        if not sum(kept.values()):
            continue
        s = sales[l["sale_id"]]
        cost = sum((Decimal(a["unit_cost"]) * kept[a["id"]] for a in allocs if a["unit_cost"] is not None), Decimal(0))
        sale = Decimal(outstanding_value(allocs, kept, l["discount_bp"], l["line_total"], l["id"],
                                         history.get(l["sale_id"], []))) / 100
        row = {"source": "program", "day": (s["completed_at"] or "")[:10], "invoice": str(s["sale_no"]),
               "customer_id": s["customer_id"], "customer": s["customer"], "ticket_name": s["ticket_name"], "ticket_ref": s["ticket_ref"],
               "ticket_no": s["ticket_no"], "store": "الصيدلية", "item_code": l["code"], "item": l["name"],
               "qty": str(sum(kept.values())), "qty_unit": "base", "sale": _egp(sale), "_sale": sale}
        if show_cost:
            known = all(a["unit_cost"] is not None for a in allocs)
            row["cost"], row["_cost"] = (_egp(cost), cost) if known else (None, None)
        lines.append(row)
    lines += _estock_claim_lines(db, ctx, contract_id, d1, d2, show_cost)
    lines.sort(key=lambda r: (r["day"], r["source"], r["invoice"]))
    # per customer (by id: two customers may share a name) and store; exact values summed, rounded once
    totals: dict = {}
    for r in lines:
        c = totals.setdefault(r["customer_id"], {"name": r["customer"], "stores": {}})
        t = c["stores"].setdefault(r["store"], {"lines": 0, "sale": Decimal(0), "cost": Decimal(0), "cost_unknown": 0})
        t["lines"] += 1
        t["sale"] += r.pop("_sale")
        cost = r.pop("_cost", None)
        if show_cost:
            if cost is None:
                t["cost_unknown"] += 1
            else:
                t["cost"] += cost
    for c in totals.values():
        for t in c["stores"].values():
            t["sale"] = _egp(t["sale"])
            if show_cost:
                t["cost"] = _egp(t["cost"])
            else:
                t.pop("cost")
                t.pop("cost_unknown")
    return {"contract": dict(k), "from": d1.isoformat(), "to": d2.isoformat(), "lines": lines, "totals": totals,
            "sources": sorted({r["source"] for r in lines}), "show_cost": show_cost}


def _store_label(name) -> str:
    """E-Stock's pharmacy store and the program's stock are the same shelf: one name in the totals
    (spelling forms ة/ه and spaces do not matter); the hospital's store keeps its own name."""
    from ..catalog.normalize import normalize_text
    key = lambda v: normalize_text(str(v or "")).replace(" ", "")   # noqa: E731
    return "الصيدلية" if key(name) == key("الصيدلية") else str(name or "")


def _estock_claim_lines(db: Database, ctx: Context, contract_id: str, d1: date, d2: date, show_cost: bool) -> list[dict]:
    from ..dataimport.estock_history import _Arch
    customers = {r["id"]: r["name"] for r in db.all("SELECT id, name FROM customers WHERE org_id=? AND contract_id=?",
                                                    (ctx.org_id, contract_id))}
    con = _archive(db, ctx) if customers else None
    if con is None:
        return []
    out = []
    with closing(con):
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # the local lines only: Sales_details, else this branch's rows of the Branches_ mirror (#345 review)
        lt, lwhere, lparams = _Arch(con).lines() if "e_Sales_header" in tables else ("", "", ())
        if not {"e_Sales_header", f"e_{lt}", "map_customers"} <= tables:
            return []
        ids = {r["estock_id"]: r["program_id"] for r in con.execute("SELECT estock_id, program_id FROM map_customers")
               if r["program_id"] in customers}
        if not ids:
            return []
        cols = lambda t: {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}   # noqa: E731
        hc, lc = cols("e_Sales_header"), cols(f"e_{lt}")
        pc = cols("e_Products") if "e_Products" in tables else {}
        sc = cols("e_Stores") if "e_Stores" in tables else {}
        h = lambda c: f'h."{hc[c]}"' if c in hc else "NULL"   # noqa: E731 - E-Stock columns may be missing
        ln = lambda c: f'l."{lc[c]}"' if c in lc else "NULL"  # noqa: E731
        stores = {r[0]: r[1] for r in con.execute(
            f'SELECT "{sc["store_id"]}", coalesce("{sc.get("store_name_ar", sc["store_id"])}", "{sc["store_id"]}") FROM "e_Stores"')} \
            if "store_id" in sc else {}
        prods = {r[0]: (r[1], r[2]) for r in con.execute(
            f'SELECT "{pc["product_id"]}", "{pc.get("product_code", pc["product_id"])}",'
            f' coalesce("{pc.get("product_name_en", pc["product_id"])}", "{pc.get("product_name_ar", pc["product_id"])}")'
            f' FROM "e_Products"')} if "product_id" in pc else {}
        q = (f"SELECT {h('sales_id')} sid, {h('bill_number')} bill, {h('insert_date')} at, {h('customer_id')} cust,"
             f" {h('store_id')} store, {h('ticket_name')} tname, {h('ticket_id')} tref, {h('ticket_num')} tno,"
             f" {ln('product_id')} pid, {ln('amount')} amount, {ln('sell_price')} sp, {ln('buy_price')} bp,"
             f" {ln('total_sell')} ts, {ln('disc_money')} dm, {ln('back_amount')} back, {ln('back_price')} bpr"
             f' FROM "e_Sales_header" h JOIN "e_{lt}" l ON l."{lc["sales_id"]}"=h."{hc["sales_id"]}"'
             f" WHERE substr({h('insert_date')}, 1, 10) BETWEEN ? AND ?"
             f" AND {h('customer_id')} IN ({','.join('?' * len(ids))}){lwhere}")
        for r in con.execute(q, (d1.isoformat(), d2.isoformat(), *ids, *lparams)):
            amt, back = _dec(r["amount"]) or Decimal(0), _dec(r["back"]) or Decimal(0)
            sp, bp, ts, dm, bpr = (_dec(r[x]) for x in ("sp", "bp", "ts", "dm", "bpr"))
            if amt - back == 0:
                continue
            gross = ts if ts is not None else amt * (sp or 0) - (dm or 0)
            sale = gross - back * (bpr if bpr is not None else (sp or 0))
            code, name = prods.get(r["pid"], (r["pid"], None))
            row = {"source": "estock", "day": (r["at"] or "")[:10], "invoice": r["bill"] or r["sid"],
                   "customer_id": ids[r["cust"]], "customer": customers[ids[r["cust"]]], "ticket_name": r["tname"], "ticket_ref": r["tref"],
                   "ticket_no": r["tno"], "store": _store_label(stores.get(r["store"], r["store"])), "item_code": code,
                   "item": name,
                   "qty": str((amt - back).normalize()), "qty_unit": "estock", "sale": _egp(sale), "_sale": sale}
            if show_cost:
                # a negative E-Stock buy price is invalid (the import leaves it unknown): an unknown cost here too
                exact = (amt - back) * bp if bp is not None and bp >= 0 else None
                row["cost"], row["_cost"] = (_egp(exact) if exact is not None else None), exact
            out.append(row)
    return out
