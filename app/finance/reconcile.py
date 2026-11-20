"""Supplier early-payment discount (F106) and party balance reconciliation (F108).

Supplier balance (supplier_ledger, + = we owe) and customer balance (customer_ledger, + = he owes us) are
checked against the party's own statement. A difference is recorded with a required type:
- opening       opening adjustment (launch balances), no profit effect          -> ledger kind 'adjustment'
- early_discount supplier only: an early-payment discount, a profit line         -> ledger kind 'discount'
- claim_discount customer only: a claim discount, lowers that month's revenue    -> ledger kind 'discount'
- correction    an error correction, no profit effect                            -> ledger kind 'adjustment'
Every check (also a matching one) is kept in party_reconciliations; every write is audited.
"""
from __future__ import annotations

import json
from datetime import date

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database, new_id

TYPES = {"opening": "تسوية افتتاحية", "early_discount": "خصم تعجيل دفع", "claim_discount": "خصم مطالبة",
         "correction": "تصحيح خطأ"}
PARTIES = {"supplier": ("supplier_ledger", "supplier_id", "suppliers", "suppliers.pay"),
           "customer": ("customer_ledger", "customer_id", "customers", "customers.manage")}
TYPE_PARTY = {"early_discount": "supplier", "claim_discount": "customer"}
PARTY_LABEL = {"supplier": "مورد", "customer": "عميل"}


def _party(party: str) -> tuple[str, str, str, str]:
    if party not in PARTIES:
        raise ValidationFailed("اختر مورد أو عميل")
    return PARTIES[party]


def _need(ctx: Context, party: str) -> None:
    """Owner / manager only: the party's money permission and seeing the expected money."""
    ctx.require(_party(party)[3])
    ctx.require("cash.see_expected")


def _int(v, what: str = "المبلغ غير صحيح") -> int:
    try:
        return whole_number(v)
    except (TypeError, ValueError):
        raise ValidationFailed(what)


def party_balance(db: Database, org_id: str, party: str, party_id: str) -> int:
    table, col, _, _ = _party(party)
    return db.one(f"SELECT coalesce(sum(amount),0) b FROM {table} WHERE org_id=? AND {col}=?", (org_id, party_id))["b"]


def _name(db: Database, ctx: Context, party: str, party_id: str) -> str:
    _, _, tbl, _ = _party(party)
    r = db.one(f"SELECT name FROM {tbl} WHERE id=? AND org_id=?", (party_id, ctx.org_id))
    if not r:
        raise NotFound("المورد غير موجود" if party == "supplier" else "العميل غير موجود")
    return r["name"]


def post_adjustment(db: Database, ctx: Context, party: str, party_id: str, amount: int, adj_type: str, *,
                    note: str | None = None, ref_id: str | None = None, statement_balance: int | None = None,
                    program_balance: int | None = None) -> str:
    """One recorded difference on a party's account, inside the caller's transaction: the ledger row, its
    party_reconciliations row and the audit row. Shared with the E-Stock import's confirmed openings
    (adj_type 'opening'). Returns the ledger row id. amount: + raises the balance, - lowers it."""
    table, col, _, perm = _party(party)
    ctx.require(perm)
    if adj_type not in TYPES:
        raise ValidationFailed("اختر نوع الفرق", code="type_required")
    if TYPE_PARTY.get(adj_type, party) != party:
        raise ValidationFailed(f"{TYPES[adj_type]} لا يخص {PARTY_LABEL[party]}")
    amount = _int(amount)
    if amount == 0:
        raise ValidationFailed("المبلغ غير صحيح")
    if adj_type in TYPE_PARTY and amount > 0:
        raise ValidationFailed("الخصم يقلل الرصيد فقط", code="discount_raises_balance")
    said = (str(note).strip() if note else "")[:200] or None
    with db.transaction():
        name = _name(db, ctx, party, party_id)
        before = party_balance(db, ctx.org_id, party, party_id) if program_balance is None else program_balance
        lid = new_id()
        kind = "discount" if adj_type in TYPE_PARTY else "adjustment"
        db.execute(f"INSERT INTO {table} (id, org_id, {col}, amount, kind, ref_id, note, created_by)"
                   " VALUES (?,?,?,?,?,?,?,?)",
                   (lid, ctx.org_id, party_id, amount, kind, ref_id,
                    TYPES[adj_type] + (f" — {said}" if said else ""), ctx.user_id))
        db.execute("INSERT INTO party_reconciliations (id, org_id, party, party_id, statement_balance, program_balance,"
                   " difference, adj_type, ledger_id, ref_id, note, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (new_id(), ctx.org_id, party, party_id, statement_balance, before, amount, adj_type, lid, ref_id,
                    said, ctx.user_id))
        audit.record(db, ctx, f"{party}.reconcile", party, party_id,
                     before={"balance": before},
                     after={"difference": amount, "type": adj_type, "statement": statement_balance, "note": said,
                            "name": name, "ledger_id": lid})
    return lid


# -------------------------------------------------------------------------- F106 early-payment discount
def split_payment(*, paid: int | None = None, discount: int | None = None, settle: int | None = None,
                  percent_bp: int | None = None) -> tuple[int, int]:
    """(paid, discount) of one supplier payment. Either the paid amount and the discount amount, or the
    amount cleared from the supplier's balance (the invoices paid off) and the discount percent in basis
    points (200 = 2%): discount = settle x percent, half up (discount_of), paid = settle - discount."""
    from ..sales.service import discount_of
    if percent_bp is not None:
        bp = _int(percent_bp, "نسبة الخصم غير صحيحة")
        if not 0 <= bp < 10000:
            raise ValidationFailed("نسبة الخصم غير صحيحة")
        s = _int(settle if settle is not None else -1)
        if s <= 0 or discount is not None or paid is not None:
            raise ValidationFailed("اكتب المبلغ اللي هيتخصم من حساب المورد مع النسبة")
        d = discount_of(s, bp)
        return s - d, d
    p = _int(paid if paid is not None else -1)
    d = _int(discount or 0, "الخصم غير صحيح")
    if p <= 0 or d < 0:
        raise ValidationFailed("المبلغ غير صحيح")
    return p, d


def supplier_discount(db: Database, ctx: Context, supplier_id: str, amount: int, *, payment_id: str,
                      note: str | None = None) -> str | None:
    """The early-payment discount of one supplier payment, inside the payment's transaction: the supplier's
    balance drops by it, no money account moves. amount 0 is a no-op."""
    ctx.require("suppliers.pay")
    amount = _int(amount, "الخصم غير صحيح")
    if amount < 0:
        raise ValidationFailed("الخصم غير صحيح")
    if not amount:
        return None
    with db.transaction():
        lid = post_adjustment(db, ctx, "supplier", supplier_id, -amount, "early_discount", note=note, ref_id=payment_id)
        audit.record(db, ctx, "supplier.discount", "supplier", supplier_id,
                     after={"amount": amount, "payment_id": payment_id, "ledger_id": lid})
    return lid


def _day_bounds(date_from: str, date_to: str) -> tuple[str, str]:
    from ..sales.service import local_day_bounds
    try:
        d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise ValidationFailed("الفترة غير صحيحة")
    if d2 < d1:
        raise ValidationFailed("الفترة غير صحيحة")
    return local_day_bounds(d1)[0], local_day_bounds(d2)[1]


def discounts_received(db: Database, org_id: str, date_from: str, date_to: str) -> int:
    """Profit-report line «خصومات مكتسبة من الموردين»: every supplier discount recorded in the period
    (a positive number of piasters)."""
    lo, hi = _day_bounds(date_from, date_to)
    return -db.one("SELECT coalesce(sum(amount),0) t FROM supplier_ledger WHERE org_id=? AND kind='discount'"
                   " AND created_at>=? AND created_at<?", (org_id, lo, hi))["t"]


def claim_discounts(db: Database, org_id: str, date_from: str, date_to: str) -> int:
    """Claim discounts given to customers in the period (positive piasters): they lower that month's revenue."""
    lo, hi = _day_bounds(date_from, date_to)
    return -db.one("SELECT coalesce(sum(amount),0) t FROM customer_ledger WHERE org_id=? AND kind='discount'"
                   " AND created_at>=? AND created_at<?", (org_id, lo, hi))["t"]


# -------------------------------------------------------------------------- F108 the reconciliation page
def parties(db: Database, ctx: Context, party: str | None = None, q: str = "", limit: int = 300) -> list[dict]:
    """One list of suppliers and customers with the program's balance and the last check, for the page where
    the manager types each party's statement balance (also the launch opening balances)."""
    kinds = [party] if party else [p for p in PARTIES if ctx.allowed(PARTIES[p][3])]
    if not kinds:
        _need(ctx, "supplier")
    for k in kinds:
        _need(ctx, k)
    from ..catalog.normalize import normalize_text
    term = normalize_text(q or "")
    limit = max(1, min(int(limit), 2000))
    out: list[dict] = []
    for k in kinds:
        table, col, tbl, _ = PARTIES[k]
        raw = f"%{(q or '').strip()}%"
        if not term:
            cond, args = "", (ctx.org_id,)
        elif k == "supplier":                                     # suppliers keep a normalized name
            cond, args = " AND (x.name_norm LIKE ? OR x.name LIKE ?)", (ctx.org_id, f"%{term}%", raw)
        else:
            cond, args = " AND (x.name LIKE ? OR x.phone LIKE ?)", (ctx.org_id, raw, raw)
        rows = db.all(
            f"SELECT x.id, x.name, coalesce((SELECT sum(l.amount) FROM {table} l WHERE l.org_id=x.org_id"
            f" AND l.{col}=x.id),0) balance,"
            " (SELECT json_object('statement_balance', r.statement_balance, 'difference', r.difference,"
            "  'adj_type', r.adj_type, 'at', r.created_at) FROM party_reconciliations r WHERE r.org_id=x.org_id"
            "  AND r.party=? AND r.party_id=x.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1) last"
            f" FROM {tbl} x WHERE x.org_id=?{cond} ORDER BY x.name LIMIT ?", (k, *args, limit))
        for r in rows:
            out.append({"party": k, "id": r["id"], "name": r["name"], "balance": r["balance"],
                        "last": json.loads(r["last"]) if r["last"] else None})
    return out[:limit]


def check(db: Database, ctx: Context, party: str, party_id: str, statement_balance: int) -> dict:
    """The difference the program would record, without recording anything."""
    _need(ctx, party)
    st = _int(statement_balance, "الرصيد غير صحيح")
    name = _name(db, ctx, party, party_id)
    bal = party_balance(db, ctx.org_id, party, party_id)
    return {"party": party, "party_id": party_id, "name": name, "program_balance": bal, "statement_balance": st,
            "difference": st - bal}


def record(db: Database, ctx: Context, party: str, party_id: str, statement_balance: int, *, program_balance: int,
           adj_type: str | None = None, note: str | None = None) -> dict:
    """Save one row of the page: the statement balance the party gave. program_balance is the balance the
    screen showed; if the account moved since, nothing is written (a retry of a saved row is refused the
    same way, so it never posts twice). A difference needs its type; every type but the opening
    adjustment needs a note."""
    _need(ctx, party)
    st = _int(statement_balance, "الرصيد غير صحيح")
    seen = _int(program_balance, "الرصيد غير صحيح")
    with db.transaction():
        name = _name(db, ctx, party, party_id)
        bal = party_balance(db, ctx.org_id, party, party_id)
        if bal != seen:
            raise Conflict("رصيد الحساب اتغير من وقت ما فتحت الصفحة؛ راجع الرقم الجديد", code="balance_changed",
                           details={"program_balance": bal})
        diff = st - bal
        lid = None
        if diff:
            if adj_type not in TYPES:
                raise ValidationFailed("يوجد فرق؛ اختر نوعه", code="type_required", details={"difference": diff})
            if adj_type != "opening" and len((note or "").strip()) < 3:
                raise ValidationFailed("اكتب ملاحظة عن الفرق", code="reason_required")
            lid = post_adjustment(db, ctx, party, party_id, diff, adj_type, note=note, statement_balance=st,
                                  program_balance=bal)
        else:
            said = (str(note).strip() if note else "")[:200] or None
            db.execute("INSERT INTO party_reconciliations (id, org_id, party, party_id, statement_balance,"
                       " program_balance, difference, note, created_by) VALUES (?,?,?,?,?,?,0,?,?)",
                       (new_id(), ctx.org_id, party, party_id, st, bal, said, ctx.user_id))
            audit.record(db, ctx, f"{party}.reconcile", party, party_id, before={"balance": bal},
                         after={"difference": 0, "statement": st, "name": name, "note": said})
    return {"party": party, "party_id": party_id, "name": name, "program_balance": bal, "statement_balance": st,
            "difference": diff, "adj_type": adj_type if diff else None, "ledger_id": lid,
            "balance": party_balance(db, ctx.org_id, party, party_id)}


def history(db: Database, ctx: Context, party: str, party_id: str) -> list[dict]:
    _need(ctx, party)
    _name(db, ctx, party, party_id)
    return [{**dict(r), "type_label": TYPES.get(r["adj_type"]) if r["adj_type"] else None} for r in db.all(
        "SELECT r.*, u.full_name by_name FROM party_reconciliations r LEFT JOIN users u ON u.id=r.created_by"
        " WHERE r.org_id=? AND r.party=? AND r.party_id=? ORDER BY r.created_at DESC, r.rowid DESC LIMIT 200",
        (ctx.org_id, party, party_id))]
