"""Opening balances from the E-Stock move, confirmed by the owner inside the program (owner 18/11, F108).

The owner tells the real balance of a supplier or customer as of the move; the program keeps E-Stock's
figure as the opening and posts the difference as an *opening adjustment* ledger row, never a payment or
an expense, audited. Later sales and payments do not matter: only the opening (E-Stock's row plus the
adjustments made here or by `--confirm`) is compared with what the owner confirms.

API route and screen: the F105 session (after #336). Service names agreed on #113:
`opening_balances.listing(db, ctx, kind)` and `opening_balances.confirm(db, ctx, kind, party_id, amount, note)`.
"""
from __future__ import annotations

import json

from ..core import audit
from ..core.errors import Forbidden, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id

REF = "estock-opening-confirm"      # ref_id of the rows posted here (estock_db.refusal: no resume after them)
NOTE = "تسوية افتتاحية: الرصيد اللي أكده المالك"
MAX_AMOUNT = 10 ** 11               # 1,000,000,000 EGP in piasters: anything bigger is a typing mistake
_KINDS = {"supplier": ("suppliers", "supplier_ledger", "supplier_id", "suppliers"),
          "customer": ("customers", "customer_ledger", "customer_id", "customers")}


def _owner(ctx: Context) -> None:
    if not ctx.is_owner:
        raise Forbidden("تأكيد الأرصدة الافتتاحية للمالك بس", code="owner_only")


def _kind(kind: str) -> tuple[str, str, str, str]:
    if kind not in _KINDS:
        raise ValidationFailed("النوع لازم يكون supplier أو customer")
    return _KINDS[kind]


def _runs(db: Database, ctx: Context, step: str) -> list[str]:
    """Applied import runs of the E-Stock move for one step ('suppliers', 'customers' or 'confirm')."""
    return [r["id"] for r in db.all("SELECT id, options FROM import_runs WHERE org_id=? AND source_system='estock_db'"
                                    " AND status='applied'", (ctx.org_id,))
            if json.loads(r["options"] or "{}").get("step") == step]


def _rows(db: Database, ctx: Context, kind: str, party_id: str | None = None) -> list[dict]:
    table, ledger, col, step = _kind(kind)
    base = _runs(db, ctx, step)
    if not base:
        return []
    opening_refs = base + _runs(db, ctx, "confirm") + [REF]
    ph_b, ph_o = ",".join("?" * len(base)), ",".join("?" * len(opening_refs))
    sql = (f"SELECT t.id, t.name, r.raw,"
           f" coalesce((SELECT sum(amount) FROM {ledger} l WHERE l.org_id=t.org_id AND l.{col}=t.id"
           f"   AND l.ref_id IN ({ph_b})), 0) estock_opening,"
           f" coalesce((SELECT sum(amount) FROM {ledger} l WHERE l.org_id=t.org_id AND l.{col}=t.id"
           f"   AND l.ref_id IN ({ph_o})), 0) opening,"
           f" coalesce((SELECT sum(amount) FROM {ledger} l WHERE l.org_id=t.org_id AND l.{col}=t.id), 0) balance_now"
           f" FROM import_rows r JOIN {table} t ON t.id=r.target_id AND t.org_id=?"
           f" WHERE r.run_id IN ({ph_b})" + (" AND t.id=?" if party_id else ""))
    params = [*base, *opening_refs, ctx.org_id, *base] + ([party_id] if party_id else [])
    out = []
    for r in db.all(sql, tuple(params)):
        raw = json.loads(r["raw"])
        out.append({"party_id": r["id"], "name": r["name"], "estock_id": raw.get("estock_id"), "estock_code": raw.get("code"),
                    "estock_opening": r["estock_opening"], "opening_adjustments": r["opening"] - r["estock_opening"],
                    "opening": r["opening"], "balance_now": r["balance_now"]})
    out.sort(key=lambda d: (-abs(d["estock_opening"]), d["name"]))
    return out


def listing(db: Database, ctx: Context, kind: str) -> list[dict]:
    """Suppliers or customers of the E-Stock move with their opening: E-Stock's figure, the adjustments
    so far, the opening now, and today's balance (amounts in piasters; + = we owe the supplier /
    the customer owes us). Biggest E-Stock balance first."""
    _owner(ctx)
    return _rows(db, ctx, kind)


def confirm(db: Database, ctx: Context, kind: str, party_id: str, amount: int, note: str | None = None) -> dict:
    """The owner's real opening balance for one party: posts (confirmed - opening now) as an opening
    adjustment. Confirming the same figure again posts nothing. Returns the party's row after it."""
    _owner(ctx)
    table, ledger, col, _ = _kind(kind)
    if isinstance(amount, bool) or not isinstance(amount, int) or abs(amount) > MAX_AMOUNT:
        raise ValidationFailed("الرصيد لازم يكون رقم صحيح بالقروش")
    note = (str(note or "").strip()[:200]) or None
    with db.transaction():
        rows = _rows(db, ctx, kind, party_id)
        if not rows:
            raise NotFound("المورد أو العميل ده مش من نقل إي ستوك")
        before = rows[0]
        diff = amount - before["opening"]
        if diff:
            db.execute(f"INSERT INTO {ledger} (id, org_id, {col}, amount, kind, ref_id, note, created_by)"
                       " VALUES (?,?,?,?, 'adjustment', ?, ?, ?)",
                       (new_id(), ctx.org_id, party_id, diff, REF, NOTE + (f" — {note}" if note else ""), ctx.user_id))
        audit.record(db, ctx, "estock.opening_confirm", table[:-1], party_id,
                     before={"opening": before["opening"], "estock_opening": before["estock_opening"]},
                     after={"opening": amount, "adjustment": diff, "note": note})
    return _rows(db, ctx, kind, party_id)[0]
