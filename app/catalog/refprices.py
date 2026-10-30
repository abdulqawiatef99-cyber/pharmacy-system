"""Public-price updates from the reference catalog (plan stage M3 = roadmap task 14).

Nothing changes a price by itself (tests: tests/test_reference_prices.py):
- propose(): for items with an APPROVED link, the newest reference price that differs from the item's
  public price becomes a pending proposal. Sources that disagree are shown as a conflict.
- apply(): a person with items.price applies chosen proposals as one batch. If the item's price changed
  since the proposal, that proposal is skipped, never overwritten. Sales and invoices keep their own
  prices; only the item's public price for future sales changes, recorded in the price history.
- rollback(): undoes a batch, item by item, unless someone changed the price again since.
- stale(): linked items whose reference price is newer than their own last price change.
"""
from __future__ import annotations

import json

from ..core import audit, notify
from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from . import matching
from . import service as cat

CONFLICT_TOLERANCE = 0.005     # 0.5 %: sources closer than this are considered to agree
MAX_APPLY = 5000


def _latest_ref_price(db: Database, ref_id: str):
    return db.one("SELECT * FROM ref_prices WHERE ref_id=? ORDER BY coalesce(effective_date,'') DESC,"
                  " observed_at DESC LIMIT 1", (ref_id,))


def propose(db: Database, ctx: Context) -> dict:
    ctx.require("items.price")
    links = db.all("SELECT l.item_id, l.ref_id, l.source, i.public_price FROM ref_links l JOIN items i"
                   " ON i.id=l.item_id AND i.org_id=l.org_id WHERE l.org_id=? AND l.status='approved'"
                   " AND i.status='active'", (ctx.org_id,))
    per_item: dict[str, list] = {}
    for l in links:
        p = _latest_ref_price(db, l["ref_id"])
        if p is not None:
            per_item.setdefault(l["item_id"], []).append((l, p))
    rep = {"proposed": 0, "conflicts": 0, "same_price": 0, "already_decided": 0, "no_reference_price": 0}
    rep["no_reference_price"] = len({l["item_id"] for l in links}) - len(per_item)
    with db.transaction():
        for item_id, cands in per_item.items():
            # the newest (by effective date, then observation) wins; the others are shown if they disagree
            cands.sort(key=lambda lp: (lp[1]["effective_date"] or "", lp[1]["observed_at"]), reverse=True)
            (l, p), others = cands[0], cands[1:]
            cur = l["public_price"]
            if cur is not None and cur == p["price"]:
                rep["same_price"] += 1
                db.execute("UPDATE ref_price_proposals SET status='superseded' WHERE org_id=? AND item_id=?"
                           " AND status='pending'", (ctx.org_id, item_id))
                continue
            if db.one("SELECT 1 FROM ref_price_proposals WHERE org_id=? AND item_id=? AND ref_price_id=?",
                      (ctx.org_id, item_id, p["id"])):
                rep["already_decided"] += 1          # pending already, or rejected before: don't nag
                continue
            conflict = [{"source": ol["source"], "price": op["price"], "effective_date": op["effective_date"]}
                        for ol, op in others if abs(op["price"] - p["price"]) > CONFLICT_TOLERANCE * max(p["price"], 1)]
            db.execute("UPDATE ref_price_proposals SET status='superseded' WHERE org_id=? AND item_id=?"
                       " AND status='pending'", (ctx.org_id, item_id))
            db.execute("INSERT INTO ref_price_proposals (id, org_id, item_id, ref_id, ref_price_id, old_price, new_price,"
                       " effective_date, verified, conflict) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (new_id(), ctx.org_id, item_id, l["ref_id"], p["id"], cur, p["price"], p["effective_date"],
                        p["verified"], json.dumps(conflict, ensure_ascii=False) if conflict else None))
            rep["proposed"] += 1
            rep["conflicts"] += bool(conflict)
    return rep


def pending(db: Database, ctx: Context, *, limit: int = 500, offset: int = 0) -> dict:
    ctx.require("items.price")
    rows = db.all("SELECT pp.*, i.name_en, i.name_ar, p.source, p.name_en ref_name FROM ref_price_proposals pp"
                  " JOIN items i ON i.id=pp.item_id AND i.org_id=pp.org_id JOIN ref_products p ON p.id=pp.ref_id"
                  " WHERE pp.org_id=? AND pp.status='pending' ORDER BY pp.conflict IS NOT NULL, i.name_en"
                  " LIMIT ? OFFSET ?", (ctx.org_id, max(1, min(int(limit), 1000)), max(0, int(offset))))
    out = []
    for r in rows:
        d = dict(r)
        d["conflict"] = json.loads(d["conflict"]) if d["conflict"] else None
        d["change_pct"] = (round((d["new_price"] - d["old_price"]) * 100 / d["old_price"], 2)
                           if d["old_price"] else None)
        out.append(d)
    total = db.one("SELECT count(*) n FROM ref_price_proposals WHERE org_id=? AND status='pending'", (ctx.org_id,))["n"]
    return {"rows": out, "total": total}


def reject(db: Database, ctx: Context, ids: list[str]) -> int:
    ctx.require("items.price")
    ids = _ids(ids)
    with db.transaction():
        n = db.execute(f"UPDATE ref_price_proposals SET status='rejected', decided_by=?,"
                       f" decided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE org_id=? AND status='pending'"
                       f" AND id IN ({','.join('?' * len(ids))})", (ctx.user_id, ctx.org_id, *ids)).rowcount
        audit.record(db, ctx, "ref_price.reject", "item", None, after={"proposals": n})
    return n


def apply(db: Database, ctx: Context, ids: list[str], *, accept_conflicts: bool = False) -> dict:
    ctx.require("items.price")
    ids = _ids(ids)
    batch = new_id()
    rep = {"batch_id": batch, "applied": 0, "skipped_changed": 0, "skipped_conflict": 0, "not_pending": 0}
    with db.transaction():
        rows = db.all(f"SELECT pp.*, i.public_price cur FROM ref_price_proposals pp JOIN items i ON i.id=pp.item_id"
                      f" AND i.org_id=pp.org_id WHERE pp.org_id=? AND pp.id IN ({','.join('?' * len(ids))})",
                      (ctx.org_id, *ids))
        rep["not_pending"] = len(ids) - len([r for r in rows if r["status"] == "pending"])
        db.execute("INSERT INTO ref_price_batches (id, org_id, applied_by, applied_at, items) VALUES"
                   " (?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),0)", (batch, ctx.org_id, ctx.user_id))
        for r in rows:
            if r["status"] != "pending":
                continue
            if r["conflict"] and not accept_conflicts:
                rep["skipped_conflict"] += 1
                continue
            if r["cur"] != r["old_price"]:
                db.execute("UPDATE ref_price_proposals SET status='skipped', note='price changed since proposal'"
                           " WHERE id=?", (r["id"],))
                rep["skipped_changed"] += 1
                continue
            db.execute("UPDATE items SET public_price=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                       " WHERE id=? AND org_id=?", (r["new_price"], r["item_id"], ctx.org_id))
            cat.record_price(db, ctx, r["item_id"], r["new_price"], r["old_price"], "reference", batch)
            db.execute("UPDATE ref_price_proposals SET status='applied', batch_id=?, decided_by=?,"
                       " decided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (batch, ctx.user_id, r["id"]))
            rep["applied"] += 1
        db.execute("UPDATE ref_price_batches SET items=? WHERE id=?", (rep["applied"], batch))
        audit.record(db, ctx, "ref_price.apply", "price_batch", batch, after=rep)
        if rep["applied"]:
            notify.emit(db, ctx, "price_change", "warning", f"تحديث أسعار من المصدر المرجعي: {rep['applied']} صنف",
                        "ممكن التراجع عن الدفعة كلها من شاشة الأسعار", "price_batch", batch)
    matching.invalidate(ctx.org_id)
    return rep


def rollback(db: Database, ctx: Context, batch_id: str) -> dict:
    ctx.require("items.price")
    rep = {"restored": 0, "skipped_changed_again": 0}
    with db.transaction():
        b = db.one("SELECT * FROM ref_price_batches WHERE id=? AND org_id=?", (batch_id, ctx.org_id))
        if not b:
            raise NotFound("الدفعة غير موجودة")
        if b["rolled_back_at"]:
            raise ValidationFailed("الدفعة اتلغت قبل كده")
        for r in db.all("SELECT pp.*, i.public_price cur FROM ref_price_proposals pp JOIN items i ON i.id=pp.item_id"
                        " AND i.org_id=pp.org_id WHERE pp.batch_id=? AND pp.status='applied'", (batch_id,)):
            if r["cur"] != r["new_price"]:
                rep["skipped_changed_again"] += 1
                continue
            db.execute("UPDATE items SET public_price=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                       " WHERE id=? AND org_id=?", (r["old_price"], r["item_id"], ctx.org_id))
            if r["old_price"] is not None:
                cat.record_price(db, ctx, r["item_id"], r["old_price"], r["new_price"], "reference_rollback", batch_id)
            db.execute("UPDATE ref_price_proposals SET status='rolled_back' WHERE id=?", (r["id"],))
            rep["restored"] += 1
        db.execute("UPDATE ref_price_batches SET rolled_back_by=?, rolled_back_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                   " WHERE id=?", (ctx.user_id, batch_id))
        audit.record(db, ctx, "ref_price.rollback", "price_batch", batch_id, after=rep)
    matching.invalidate(ctx.org_id)
    return rep


def batches(db: Database, ctx: Context, limit: int = 50) -> list[dict]:
    ctx.require("items.price")
    return [dict(r) for r in db.all("SELECT * FROM ref_price_batches WHERE org_id=? ORDER BY applied_at DESC LIMIT ?",
                                    (ctx.org_id, max(1, min(int(limit), 500))))]


def stale(db: Database, ctx: Context, *, older_than_days: int = 30) -> dict:
    """Linked items whose reference price is newer than their own last price change, and reference
    prices nobody has re-observed for a while (the source may have stopped updating)."""
    ctx.require("items.read")
    rows = db.all(
        "SELECT l.item_id, i.name_en, i.public_price, l.source,"
        " (SELECT max(h.observed_at) FROM item_price_history h WHERE h.org_id=l.org_id AND h.item_id=l.item_id) item_changed,"
        " (SELECT max(r.effective_date) FROM ref_prices r WHERE r.ref_id=l.ref_id) ref_effective,"
        " (SELECT max(r.observed_at) FROM ref_prices r WHERE r.ref_id=l.ref_id) ref_observed,"
        " p.last_seen_at FROM ref_links l JOIN items i ON i.id=l.item_id AND i.org_id=l.org_id"
        " JOIN ref_products p ON p.id=l.ref_id WHERE l.org_id=? AND l.status='approved'", (ctx.org_id,))
    behind, source_quiet = [], []
    for r in rows:
        d = dict(r)
        if d["ref_effective"] and (not d["item_changed"] or d["ref_effective"] > d["item_changed"][:10]):
            behind.append(d)
        if d["last_seen_at"] and db.one("SELECT julianday('now') - julianday(?) > ? q", (d["last_seen_at"], older_than_days))["q"]:
            source_quiet.append(d)
    return {"item_price_older_than_reference": behind, "reference_not_seen_recently": source_quiet,
            "days": older_than_days}


def _ids(ids) -> list[str]:
    if not isinstance(ids, list) or not ids or len(ids) > MAX_APPLY or not all(isinstance(i, str) for i in ids):
        raise ValidationFailed("اختار الاقتراحات (من 1 لحد 5000)")
    return list(dict.fromkeys(ids))
