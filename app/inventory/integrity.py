"""Stock integrity check (#202): recompute every balance from the stock ledger and compare.

Runs on startup and after every automatic backup, and on demand from the owner's report
«مطابقة الأرصدة». It only READS the stock tables. A mismatch is never repaired here: it raises a red
alert (an error notification + the banner every owner/manager sees) and appends an audit row, and the
owner decides the fix (docs/STOCK_INTEGRITY.md §5). When a later check is clean again, an audit row
records that too, which clears the banner.

What is compared, per pharmacy (org):
- batches:  each (branch, item, batch) balance vs the sum of its movements (inventory.verify_balances)
- items:    each (branch, item) total vs the sum of all its movements
- negative: any balance below zero (post_movement refuses them, so one means outside damage)
- foreign_batch: a movement whose batch belongs to another item or pharmacy
- sales:    for every (sale, item) that moved stock, the net units moved vs what the invoice says
            (sold minus returned; zero for a voided invoice)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading

from ..core import audit, notify
from ..core.accounts import iso, utcnow
from ..core.permissions import Context
from ..db import Database
from . import service as inventory

log = logging.getLogger("pharm.integrity")

MISMATCH, CLEAN = "stock.integrity_mismatch", "stock.integrity_ok"
# A sale whose stock moved differently from its invoice stays different forever (the ledger is append-only), so
# once the shelf was corrected by a count the owner marks that exact row as reviewed, with a note (audited).
ACK = "stock.integrity_ack"
KINDS = ("batches", "items", "negative", "foreign_batch", "sales")
# One check at a time (startup, backup and the owner's report can overlap): an older clean scan must never clear
# an alert that a newer scan recorded in between (Codex P2 on #213).
_LOCK = threading.Lock()
SAMPLE = 50          # rows of each kind kept in the audit row / shown in the report


def _items(db: Database, org_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT branch_id, item_id, sum(lq) ledger_qty, sum(bq) balance_qty"
        " FROM (SELECT branch_id, item_id, qty_delta lq, 0 bq FROM stock_movements WHERE org_id=?"
        "       UNION ALL SELECT branch_id, item_id, 0, qty FROM stock_balances WHERE org_id=?)"
        " GROUP BY branch_id, item_id HAVING sum(lq) <> sum(bq)", (org_id, org_id))]


def _negative(db: Database, org_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT branch_id, item_id, batch_key, qty FROM stock_balances WHERE org_id=? AND qty < 0", (org_id,))]


def _foreign_batch(db: Database, org_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT m.id movement_id, m.item_id, m.batch_id,"
        " CASE WHEN b.org_id = m.org_id THEN b.item_id END batch_item_id FROM stock_movements m"   # never another org's id
        " JOIN batches b ON b.id = m.batch_id WHERE m.org_id=? AND (b.item_id <> m.item_id OR b.org_id <> m.org_id)",
        (org_id,))]


def _sales(db: Database, org_id: str) -> list[dict]:
    # Per (sale, item) that moved stock: net units moved by the sale, its returns and its void must equal what the
    # invoice says (returned minus sold; zero once voided). A pair with no movement is not judged here, because
    # whether an item is a service can change later (the write-path tests prove a sale always posts them).
    return [dict(r) for r in db.all(
        "WITH mv AS (SELECT ref_id sale_id, item_id, sum(qty_delta) moved FROM stock_movements"
        "            WHERE org_id=? AND ref_type IN ('sale','sale_void') GROUP BY ref_id, item_id),"
        # sold = the invoice lines themselves (not the allocations, which are written next to the movements);
        # each side aggregated once and joined, never a per-line subquery (Codex P2 on #209: that was quadratic)
        " sd AS (SELECT sale_id, item_id, sum(qty_base) sold FROM sale_lines WHERE org_id=? GROUP BY sale_id, item_id),"
        " rt AS (SELECT l.sale_id, l.item_id, sum(a.returned_qty) returned FROM sale_allocations a"
        "        JOIN sale_lines l ON l.id = a.sale_line_id WHERE l.org_id=? GROUP BY l.sale_id, l.item_id)"
        " SELECT s.id sale_id, s.sale_no, s.status, mv.item_id, mv.moved,"
        "  CASE WHEN s.status='completed' THEN coalesce(rt.returned,0) - coalesce(sd.sold,0) ELSE 0 END expected"
        " FROM mv JOIN sales s ON s.id = mv.sale_id AND s.org_id=?"
        " LEFT JOIN sd ON sd.sale_id = mv.sale_id AND sd.item_id = mv.item_id"
        " LEFT JOIN rt ON rt.sale_id = mv.sale_id AND rt.item_id = mv.item_id"
        " WHERE mv.moved <> CASE WHEN s.status='completed' THEN coalesce(rt.returned,0) - coalesce(sd.sold,0) ELSE 0 END",
        (org_id, org_id, org_id, org_id))]


def _ack_key(r: dict) -> tuple:
    return (r["sale_id"], r["item_id"], r["moved"], r["expected"])


def _acknowledged(db: Database, org_id: str) -> set[tuple]:
    out = set()
    for a in db.all("SELECT after_json FROM audit_log WHERE org_id=? AND action=?", (org_id, ACK)):
        d = json.loads(a["after_json"])
        out.add(_ack_key(d))
    return out


def scan(db: Database, org_id: str) -> dict:
    """Read-only. {'ok', 'checked_at', 'counts': {kind: n}, kind: [first SAMPLE rows]}"""
    sales = _sales(db, org_id)
    acked = _acknowledged(db, org_id) if sales else set()
    found = {"batches": inventory.verify_balances(db, org_id), "items": _items(db, org_id),
             "negative": _negative(db, org_id), "foreign_batch": _foreign_batch(db, org_id),
             # a reviewed row stays out only while its numbers are exactly the ones reviewed
             "sales": [r for r in sales if _ack_key(r) not in acked]}
    out = {"ok": not any(found.values()), "checked_at": iso(utcnow()),
           "counts": {k: len(v) for k, v in found.items()},
           "movements": db.one("SELECT count(*) n FROM stock_movements WHERE org_id=?", (org_id,))["n"],
           "balances": db.one("SELECT count(*) n FROM stock_balances WHERE org_id=?", (org_id,))["n"]}
    out.update({k: v[:SAMPLE] for k, v in found.items()})
    return out


def _fingerprint(result: dict) -> str:
    keys = {k: sorted(json.dumps(r, sort_keys=True) for r in result[k]) for k in KINDS}
    return hashlib.sha256(json.dumps([result["counts"], keys], sort_keys=True).encode()).hexdigest()[:16]


def last_state(db: Database, org_id: str) -> dict | None:
    # Both outcomes are recorded on entity "system" (see _check), so ix_audit_org_entity finds them. Without the
    # entity a pharmacy that never had a mismatch has no such row and the whole audit log was read each time:
    # every owner screen asks once a minute (#253: 2.2 s on 2.1 M rows, on the event loop). "+seq": order the
    # few rows found, do not walk the log backwards looking for them.
    r = db.one("SELECT action, after_json, at FROM audit_log WHERE org_id=? AND entity_type='system'"
               " AND entity_id IS NULL AND action IN (?,?) ORDER BY +seq DESC LIMIT 1", (org_id, MISMATCH, CLEAN))
    if not r:
        return None
    after = json.loads(r["after_json"]) if r["after_json"] else {}
    return {"alert": r["action"] == MISMATCH, "at": r["at"], **after}


def _system_ctx(org_id: str) -> Context:
    return Context(user_id="system", org_id=org_id, branch_id=None)


def check(db: Database, org_id: str, reason: str, actor: Context | None = None) -> dict:
    """Scan one pharmacy and record the outcome (audit row on mismatch, notification when the mismatch is new,
    audit row when it is clean again). Never touches the stock tables. actor: who asked (the owner's report);
    None = the program itself."""
    with _LOCK:
        return _check(db, org_id, reason, actor)


def _check(db: Database, org_id: str, reason: str, actor: Context | None) -> dict:
    result = scan(db, org_id)
    prev = last_state(db, org_id)
    fp = _fingerprint(result)
    ctx = actor or _system_ctx(org_id)
    if not result["ok"]:
        log.error("stock integrity mismatch (%s): %s", reason, result["counts"])
        with db.transaction():
            audit.record(db, ctx, MISMATCH, "system", None, after={
                "reason": reason, "counts": result["counts"], "fingerprint": fp,
                **{k: result[k][:10] for k in KINDS if result[k]}})
            if not (prev and prev.get("alert") and prev.get("fingerprint") == fp):
                n = sum(result["counts"].values())
                notify.emit(db, ctx, "stock_integrity", "error",
                            "تنبيه: أرصدة المخزون لا تطابق حركات المخزون",
                            f"{n} اختلاف — افتح تقرير «مطابقة الأرصدة». لم يُصلَّح أي رصيد تلقائيًا.",
                            "system", None)
    elif prev and prev.get("alert"):
        with db.transaction():
            audit.record(db, ctx, CLEAN, "system", None,
                         after={"reason": reason, "movements": result["movements"]})
    result["fingerprint"] = fp
    return result


def check_all(db: Database, reason: str) -> dict[str, dict]:
    """Every pharmacy on this node. A failure of the check itself is logged, never raised: it must not stop the
    program from starting or a backup from finishing."""
    out = {}
    for o in db.all("SELECT id FROM organizations"):
        try:
            out[o["id"]] = check(db, o["id"], reason)
        except Exception:
            log.exception("stock integrity check failed to run (%s)", reason)
    return out


def banner(db: Database, ctx: Context) -> dict | None:
    """What the red banner shows, for whoever may see it (owner and managers: audit.read)."""
    if not ctx.can("audit.read"):
        return None
    st = last_state(db, ctx.org_id)
    if not st or not st.get("alert"):
        return None
    return {"at": st["at"], "counts": st.get("counts", {}), "reason": st.get("reason")}


def report(db: Database, ctx: Context) -> dict:
    """The owner's report «مطابقة الأرصدة»: runs the check now (recorded like any other run, by this user) and
    returns it with names, plus the recent history of checks that found or cleared a mismatch."""
    ctx.require("audit.read")
    r = check(db, ctx.org_id, "manual", actor=ctx)
    names: dict = {}

    def item(iid):
        if iid not in names:
            x = db.one("SELECT name_en, name_ar, code FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
            names[iid] = {"name": (x["name_en"] or x["name_ar"]) if x else None, "code": x["code"] if x else None}
        return names[iid]

    def batch(bid):
        if not bid:
            return {"batch_no": None, "expiry_date": None}
        # scoped to the pharmacy asking: a foreign_batch row may point at another pharmacy's batch (Codex P1)
        b = db.one("SELECT batch_no, expiry_date FROM batches WHERE id=? AND org_id=?", (bid, ctx.org_id))
        return {"batch_no": b["batch_no"] if b else None, "expiry_date": b["expiry_date"] if b else None}

    for k in KINDS:
        rows = []
        for row in r[k]:
            row = dict(row)
            row.update(item(row["item_id"]))
            if k in ("batches", "negative"):
                row.update(batch(row.get("batch_key")))
            elif k == "foreign_batch":
                row.update(batch(row.get("batch_id")))
            rows.append(row)
        r[k] = rows
    r["items_checked"] = db.one("SELECT count(DISTINCT item_id) n FROM stock_balances WHERE org_id=?",
                                (ctx.org_id,))["n"]
    r["history"] = [{"at": h["at"], "ok": h["action"] == CLEAN, "ack": h["action"] == ACK,
                     "who": h["full_name"] or "البرنامج",
                     **{k: v for k, v in (json.loads(h["after_json"]) if h["after_json"] else {}).items()
                        if k in ("reason", "counts", "note", "sale_no")}}
                    for h in db.all("SELECT a.at, a.action, a.after_json, u.full_name FROM audit_log a"
                                    " LEFT JOIN users u ON u.id = a.user_id WHERE a.org_id=? AND a.action IN (?,?,?)"
                                    " ORDER BY a.seq DESC LIMIT 20", (ctx.org_id, MISMATCH, CLEAN, ACK))]
    return r


def acknowledge(db: Database, ctx: Context, sale_id: str, item_id: str, note: str, *,
                moved: int | None = None, expected: int | None = None) -> dict:
    """The owner marks one sale-vs-invoice difference as reviewed (the shelf was corrected by a stock count, which
    posts its own movements). Only that kind: a balance that differs from its ledger is never acknowledged away."""
    from ..core.errors import NotFound, ValidationFailed
    ctx.require("settings.manage")
    note = (note or "").strip()
    if len(note) < 5:
        raise ValidationFailed("اكتب ما تمت مراجعته (مثلًا: جرد الصنف بتاريخ …)")
    with _LOCK:
        row = next((r for r in _sales(db, ctx.org_id) if r["sale_id"] == sale_id and r["item_id"] == item_id), None)
        if not row:
            raise NotFound("لا يوجد اختلاف لهذه الفاتورة وهذا الصنف")
        # only the numbers the owner saw: if the sale moved again since the report loaded, he reviews it again
        if (moved, expected) != (row["moved"], row["expected"]):
            from ..core.errors import Conflict
            raise Conflict("أرقام هذا السطر تغيّرت منذ فتح التقرير — حدّث التقرير وراجعه من جديد",
                           code="stock_check_changed")
        with db.transaction():
            audit.record(db, ctx, ACK, "sale", sale_id, after={
                "sale_id": sale_id, "item_id": item_id, "moved": row["moved"], "expected": row["expected"],
                "sale_no": row["sale_no"], "note": note[:300]})
        return _check(db, ctx.org_id, "manual", ctx)
