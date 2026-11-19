"""Return against an old E-Stock invoice (ج1). Design: docs/specs/09-estock-old-invoice-returns.md.

The invoice lives in the read-only E-Stock archive, not in `sales`, so `Sales.return_items` cannot take it.
Here: the line is found in the archive (this pharmacy's only), what was already returned counts from E-Stock and
from the program, the money goes back the way it came in and never more than was received, the stock goes back
into the item's batch for that E-Stock batch with its original expiry and cost, and one audit record holds it
all. Idempotent by the caller's key. Backend only: no route, no screen yet.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from ..core import audit
from ..core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from ..inventory import service as inventory
from . import cash
from .service import return_window_days

REF = "estock_sale_return"
ACTION = "estock.sale_return"
COST_Q = Decimal("0.000001")


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v).strip()) if v not in (None, "") else None
    except InvalidOperation:
        return None
    return d if d is not None and d.is_finite() else None


def _piasters(d: Decimal) -> int:
    """EGP -> piasters, half up (the program's one rounding mode, as discount_of)."""
    return int((d * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def _ident(v) -> str:
    s = str(v or "").strip()
    d = _dec(s)
    return str(int(d)) if d is not None and d == d.to_integral_value() else s


def _archive(db: Database, ctx: Context) -> sqlite3.Connection:
    from ..dataimport import estock_archive
    if db.path == ":memory:":
        raise NotFound("مفيش تاريخ إي ستوك", code="no_estock_history")
    con = estock_archive.open_for(Path(db.path).parent / estock_archive.NAME, ctx.org_id)
    con.row_factory = sqlite3.Row
    return con


def _cols(con, t: str) -> dict[str, str]:
    return {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("e_{t}")')}


def _has(con, t: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (f"e_{t}",)).fetchone())


def _rows(con, t: str, where: dict) -> list[dict]:
    """Rows of an archived table whose columns equal `where` (E-Stock ids compared as ids: 1 = 1.0)."""
    if not _has(con, t):
        return []
    c = _cols(con, t)
    out = []
    for r in con.execute(f'SELECT * FROM "e_{t}"'):
        d = {k.lower(): r[k] for k in r.keys()}
        if all(_ident(d.get(k)) == v for k, v in where.items() if k in c):
            out.append(d)
    return out


def _local_lines(con, sales_id: str) -> list[dict]:
    """The invoice's lines from the local table: Sales_details when it was read, else this branch's mirror."""
    if _has(con, "Sales_details") and con.execute('SELECT 1 FROM "e_Sales_details" LIMIT 1').fetchone():
        return _rows(con, "Sales_details", {"sales_id": sales_id})
    branch = None
    if _has(con, "co_inf"):
        r = con.execute('SELECT * FROM "e_co_inf" LIMIT 1').fetchone()
        if r is not None:
            branch = _ident({k.lower(): r[k] for k in r.keys()}.get("branch_id")) or None
    lines = _rows(con, "Branches_sales_details", {"sales_id": sales_id})
    return [l for l in lines if branch is None or _ident(l.get("branch_id")) == branch]


def _map(con, kind: str) -> dict[str, str]:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (f"map_{kind}",)).fetchone():
        return {}
    return {r[0]: r[1] for r in con.execute(f'SELECT estock_id, program_id FROM "map_{kind}"')}


def _invoice(db: Database, ctx: Context, sales_id: str) -> dict:
    """Everything a return needs, read from the archive (no write)."""
    with closing(_archive(db, ctx)) as con:
        heads = _rows(con, "Sales_header", {"sales_id": sales_id})
        if not heads:
            raise NotFound("الفاتورة دي مش في تاريخ إي ستوك")
        h = heads[0]
        items, customers = _map(con, "items"), _map(con, "customers")
        lines = _local_lines(con, sales_id)
        back_lines = _rows(con, "Back_Sales_details", {"sales_id": sales_id})
        back_heads = _rows(con, "Back_sales_header", {"sales_id": sales_id})
    # E-Stock's return documents name the product, not the line: what they hold beyond the lines' own
    # back_amount is spread over that product's lines in order, once (#346 review), never on each line
    extra: dict[str, Decimal] = defaultdict(Decimal)
    for b in back_lines:
        extra[_ident(b.get("product_id"))] += _dec(b.get("back_amount")) or 0
    for l in lines:
        extra[_ident(l.get("product_id"))] -= _dec(l.get("back_amount")) or 0
    out_lines = {}
    for n, l in enumerate(lines, start=1):
        key = _ident(l.get("details_id")) or str(n)
        pid = _ident(l.get("product_id"))
        iid = items.get(pid)
        item = db.one("SELECT id, pack_size, is_service, coalesce(name_en, name_ar) name FROM items WHERE id=? AND org_id=?",
                      (iid, ctx.org_id)) if iid else None
        amt, back = _dec(l.get("amount")) or Decimal(0), _dec(l.get("back_amount")) or Decimal(0)
        take = max(Decimal(0), min(extra[pid], amt - back))
        extra[pid] -= take
        ts = _dec(l.get("total_sell"))
        net = ts if ts is not None else amt * (_dec(l.get("sell_price")) or 0) - (_dec(l.get("disc_money")) or 0)
        pack = item["pack_size"] if item else 1
        sold, ret = amt * pack, (back + take) * pack
        out_lines[key] = {
            "line_key": key, "product_id": pid, "item_id": item["id"] if item else None, "item": item["name"] if item else None,
            "is_service": bool(item["is_service"]) if item else False, "pack": pack,
            "sold_units": int(sold) if sold == sold.to_integral_value() else None,
            "estock_returned_units": int(ret) if ret == ret.to_integral_value() else None,
            "net": _piasters(net), "expiry": str(l.get("exp_date") or "")[:10] or None,
            "unit_cost": ((_dec(l.get("buy_price")) or 0) / pack).quantize(COST_Q, ROUND_HALF_UP)
            if _dec(l.get("buy_price")) is not None and _dec(l.get("buy_price")) >= 0 else None,
            "batch_no": f"{_ident(h.get('store_id'))}-{_ident(l.get('counter_id'))}" if _ident(l.get("counter_id")) else None}
    net = _dec(h.get("total_bill_net")) or Decimal(0)
    card = max(Decimal(0), _dec(h.get("network_money")) or Decimal(0))
    paid_cash = max(Decimal(0), (_dec(h.get("bill_cash")) or 0) - (_dec(h.get("money_change")) or 0))
    paid_cash = min(paid_cash, max(Decimal(0), net - card))
    account = max(Decimal(0), net - paid_cash - card)
    return {"sales_id": sales_id, "day": str(h.get("insert_date") or "")[:10],
            "customer_id": customers.get(_ident(h.get("customer_id"))), "lines": out_lines,
            "parts": {"account": _piasters(account), "cash": _piasters(paid_cash), "card": _piasters(card)},
            "estock_refunded": sum(_piasters(_dec(b.get("total_bill_net")) or Decimal(0)) for b in back_heads)}


def _program_history(db: Database, ctx: Context, sales_id: str) -> list[dict]:
    return [json.loads(r["after_json"]) for r in db.all(
        "SELECT after_json FROM audit_log WHERE org_id=? AND action=? AND entity_type='estock_sale' AND entity_id=?"
        " ORDER BY rowid", (ctx.org_id, ACTION, sales_id))]


def _returned_in_program(hist: list[dict], key: str) -> int:
    """Units of the line the program already took back, from the audit records of its returns: a service line
    has no stock movement, so the movements cannot count it (#346 review)."""
    return sum(int(hh.get("lines", {}).get(key, 0)) for hh in hist)


def invoice_lines(db: Database, ctx: Context, sales_id: str) -> list[dict]:
    """The old invoice's lines with what was sold, what came back (E-Stock + program) and the line's net."""
    ctx.require("sales.return")
    inv = _invoice(db, ctx, str(sales_id))
    hist = _program_history(db, ctx, str(sales_id))
    out = []
    for k, l in inv["lines"].items():
        refunded = sum(int(hh.get("line_refunds", {}).get(k, 0)) for hh in hist)
        out.append({**{x: l[x] for x in ("line_key", "item_id", "item", "sold_units", "net", "expiry")},
                    "returned_units": (l["estock_returned_units"] or 0) + _returned_in_program(hist, k),
                    "refunded_in_program": refunded})
    return out


def _left(inv: dict, hist: list[dict]) -> dict[str, int]:
    """What is still refundable from each part: the invoice's parts less E-Stock's refunds (account part first,
    then cash, then card) and the program's earlier returns of this invoice."""
    left = dict(inv["parts"])
    rest = inv["estock_refunded"]
    for part in ("account", "cash", "card"):
        take = min(rest, left[part])
        left[part] -= take
        rest -= take
    for hh in hist:
        for part, v in hh.get("parts", {}).items():
            left[part] -= int(v)
    return left


def return_items(db: Database, ctx: Context, sales_id: str, lines: dict, *, reason: str, key: str) -> dict:
    """lines: {line_key: base_units}. Returns {return_id, refund, parts, lines}; the same key twice returns the
    first result and writes nothing."""
    ctx.require("sales.return")
    ctx.require("sales.return_others")            # an E-Stock invoice was never sold by a program user
    sales_id = str(sales_id)
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValidationFailed("مفتاح العملية مطلوب", code="key_required")
    if len((reason or "").strip()) < 3:
        raise ValidationFailed("اكتب سبب المرتجع")
    if not isinstance(lines, dict) or not lines:
        raise ValidationFailed("اختر الأصناف والكميات المرتجعة", code="empty_return")
    for k, q in lines.items():
        if isinstance(q, bool) or not isinstance(q, int) or q <= 0:
            raise ValidationFailed("كمية المرتجع غير صحيحة")
    inv = _invoice(db, ctx, sales_id)
    try:
        age = (date.today() - date.fromisoformat(inv["day"])).days
    except ValueError:
        age = 10 ** 6
    if age > return_window_days(db, ctx.org_id):
        ctx.require("sales.return_late")
    with db.transaction():
        hist = _program_history(db, ctx, sales_id)
        same = next((hh for hh in hist if hh.get("key") == key), None)
        if same:
            return {k: same[k] for k in ("return_id", "refund", "parts", "lines")}
        refund, line_refunds = 0, {}
        for k, q in lines.items():
            l = inv["lines"].get(str(k))
            if not l:
                raise NotFound("السطر ده مش في الفاتورة")
            if not l["item_id"]:
                raise ValidationFailed("صنف الفاتورة القديمة مش مربوط بصنف في البرنامج", code="unmapped_item")
            nr = db.one("SELECT c.name FROM items i JOIN categories c ON c.id=i.category_id"
                        " WHERE i.id=? AND i.org_id=? AND c.no_return=1", (l["item_id"], ctx.org_id))
            if nr and not ctx.allowed("sales.return_restricted"):     # as Sales.return_items (#346 review)
                raise Forbidden(f"هذا الصنف من قسم «{nr['name']}» ولا يُقبل إرجاعه ولا استبداله",
                                code="approval_required",
                                details={"permission": "sales.return_restricted",
                                         "label": f"مرتجع صنف من قسم «{nr['name']}»"})
            if l["sold_units"] is None or l["estock_returned_units"] is None:
                raise ValidationFailed("كمية السطر في إي ستوك مش عدد صحيح من الوحدات", code="fractional_line")
            done = l["estock_returned_units"] + _returned_in_program(hist, str(k))
            left_units = l["sold_units"] - done
            if q > left_units:
                raise Conflict(f"الباقي من السطر ده {max(0, left_units)} وحدة بس", code="return_exceeds_sold",
                               details={"left": max(0, left_units)})
            already = sum(int(hh.get("line_refunds", {}).get(str(k), 0)) for hh in hist)
            # E-Stock's own returns of the line were refunded at its price: what remains of the line's net
            est = _piasters(Decimal(l["net"]) * l["estock_returned_units"] / l["sold_units"] / 100) \
                if l["sold_units"] else 0
            rest_net = l["net"] - est - already
            if q == left_units:
                value = rest_net                       # the return that empties the line settles it exactly
            else:
                value = min(rest_net, _piasters(Decimal(l["net"]) * q / l["sold_units"] / 100))
            value = max(0, value)
            line_refunds[str(k)] = value
            refund += value
        left = _left(inv, hist)
        parts, rest = {"account": 0, "cash": 0, "card": 0}, refund
        for part in ("account", "cash", "card"):       # a debt is reduced first, never turned into cash
            if part == "account" and not inv["customer_id"]:
                continue
            take = max(0, min(rest, left[part]))
            parts[part] = take
            rest -= take
        if rest > 0:
            raise Conflict("المرتجع أكبر من الفلوس اللي اتدفعت في الفاتورة دي", code="refund_exceeds_received",
                           details={"refund": refund, "left": left})
        rid = new_id()
        for k, q in lines.items():
            l = inv["lines"][str(k)]
            if l["is_service"]:
                continue
            bid = inventory.get_or_create_batch(db, ctx, l["item_id"], l["batch_no"], l["expiry"],
                                                "day" if l["expiry"] else None)
            inventory.post_movement(db, ctx, item_id=l["item_id"], qty_delta=q, movement_type="sale_return",
                                    batch_id=bid, unit_cost=l["unit_cost"], ref_type=REF, ref_id=sales_id,
                                    ref_line_id=str(k), note=f"مرتجع فاتورة إي ستوك {sales_id}: {reason.strip()}")
        if parts["account"]:
            if not db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (inv["customer_id"], ctx.org_id)):
                raise NotFound("عميل الفاتورة مش موجود")
            db.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind, ref_id, note, created_by)"
                       " VALUES (?,?,?,?, 'return', ?, ?, ?)",
                       (new_id(), ctx.org_id, inv["customer_id"], -parts["account"], rid,
                        f"مرتجع فاتورة إي ستوك {sales_id}", ctx.user_id))
        if parts["cash"]:
            cash.record(db, ctx, -parts["cash"], "refund", rid, f"مرتجع فاتورة إي ستوك {sales_id}")
        out = {"return_id": rid, "refund": refund, "parts": parts, "lines": {str(k): q for k, q in lines.items()}}
        audit.record(db, ctx, ACTION, "estock_sale", sales_id,
                     after={**out, "key": key, "reason": reason.strip(), "line_refunds": line_refunds,
                            "customer_id": inv["customer_id"]})
    return out
