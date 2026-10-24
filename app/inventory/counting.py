"""Inventory counting (جرد).

* A session has a scope: full, by location(s), or a list of items.
* Counters record quantities by scanning/choosing an item (optionally a batch
  and a location). One live entry per item+batch+location: a second person
  counting the same thing gets 'already_counted' and must explicitly recount
  (the old entry is kept, superseded) — no silent double counting.
* Blind counting: counters don't see system quantities.
* Sales during the count are handled: each entry remembers the ledger
  position when it was counted; expected quantity now = counted + movements
  posted after that position.
* Approval (inventory.adjust) posts 'adjustment' movements for the variances.
  Uncounted items are NOT zeroed unless the approver explicitly says so.
"""
from __future__ import annotations

import json
from decimal import Decimal

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id
from . import service as inventory


def _now() -> str:
    from ..core.accounts import iso, utcnow
    return iso(utcnow())


class Counting:
    def __init__(self, db: Database):
        self.db = db

    def create(self, ctx: Context, *, title: str, scope: str = "full",
               locations: list[str] | None = None, item_ids: list[str] | None = None,
               blind: bool = True) -> str:
        ctx.require("inventory.adjust")
        if scope not in ("full", "location", "items"):
            raise ValidationFailed("نطاق جرد غير صحيح")
        flt: dict = {}
        if scope == "location":
            if not locations:
                raise ValidationFailed("حدد المواقع")
            flt["locations"] = [l.strip() for l in locations if l.strip()]
        if scope == "items":
            if not item_ids:
                raise ValidationFailed("حدد الأصناف")
            for i in item_ids:
                if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (i, ctx.org_id)):
                    raise NotFound("صنف غير موجود في نطاق الجرد")
            flt["item_ids"] = list(dict.fromkeys(item_ids))
        if self.db.one("SELECT 1 FROM count_sessions WHERE org_id=? AND branch_id=? AND status='open'"
                       " AND scope='full'", (ctx.org_id, ctx.branch_id)) and scope == "full":
            raise Conflict("يوجد جرد شامل مفتوح بالفعل")
        sid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO count_sessions (id, org_id, branch_id, title, scope,"
                            " scope_filter, blind, created_by) VALUES (?,?,?,?,?,?,?,?)",
                            (sid, ctx.org_id, ctx.branch_id, title.strip() or "جرد", scope,
                             json.dumps(flt, ensure_ascii=False), int(blind), ctx.user_id))
            audit.record(self.db, ctx, "count.create", "count_session", sid,
                         after={"scope": scope, "filter": flt})
        return sid

    def _session(self, ctx: Context, sid: str, *, open_only: bool = False) -> dict:
        s = self.db.one("SELECT * FROM count_sessions WHERE id=? AND org_id=?", (sid, ctx.org_id))
        if not s:
            raise NotFound("الجرد غير موجود")
        if open_only and s["status"] != "open":
            raise Conflict("الجرد مغلق", code="session_closed")
        d = dict(s)
        d["scope_filter"] = json.loads(d["scope_filter"])
        return d

    def _in_scope(self, s: dict, item: dict) -> bool:
        f = s["scope_filter"]
        if s["scope"] == "items":
            return item["id"] in f["item_ids"]
        if s["scope"] == "location":
            return (item.get("location") or "") in f["locations"]
        return True

    def record(self, ctx: Context, sid: str, *, item_id: str, qty: int, batch_id: str | None = None,
               location: str = "", mode: str = "set", recount: bool = False,
               device: str | None = None) -> dict:
        ctx.require("inventory.count")
        if mode not in ("set", "add"):
            raise ValidationFailed("وضع غير صحيح")
        if not isinstance(qty, int) or qty < 0:
            raise ValidationFailed("الكمية لازم تكون رقم صحيح موجب")
        with self.db.transaction():
            s = self._session(ctx, sid, open_only=True)
            item = self.db.one("SELECT * FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
            if not item:
                raise NotFound("الصنف غير موجود")
            if not self._in_scope(s, dict(item)):
                raise ValidationFailed("الصنف خارج نطاق هذا الجرد", code="out_of_scope")
            if batch_id and not self.db.one("SELECT 1 FROM batches WHERE id=? AND item_id=? AND org_id=?",
                                            (batch_id, item_id, ctx.org_id)):
                raise NotFound("التشغيلة غير موجودة لهذا الصنف")
            key = batch_id or ""
            loc = (location or "").strip()
            live = self.db.one("SELECT * FROM count_entries WHERE session_id=? AND item_id=? AND"
                               " batch_key=? AND location=? AND superseded_by IS NULL",
                               (sid, item_id, key, loc))
            new_qty = qty
            if live:
                if mode == "add" and live["counted_by"] == ctx.user_id:
                    new_qty = live["qty"] + qty
                elif live["counted_by"] != ctx.user_id and not recount:
                    raise Conflict("الصنف ده اتجرد قبل كده في نفس المكان", code="already_counted",
                                   details={"qty": live["qty"], "counted_at": live["counted_at"]})
            eid = new_id()
            seq = self.db.one("SELECT coalesce(max(rowid),0) s FROM stock_movements")["s"]
            if live:
                self.db.execute("UPDATE count_entries SET superseded_by=? WHERE id=?", (eid, live["id"]))
            self.db.execute("INSERT INTO count_entries (id, session_id, org_id, item_id, batch_key,"
                            " location, qty, counted_by, device, counted_at, ledger_seq)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (eid, sid, ctx.org_id, item_id, key, loc, new_qty, ctx.user_id, device,
                             _now(), seq))
        out = {"entry_id": eid, "qty": new_qty}
        if not s["blind"]:
            out["system_qty"] = inventory.stock_for_item(self.db, ctx, item_id)["total_qty"] \
                if ctx.can("inventory.read") else None
        return out

    def _moves_after(self, ctx: Context, branch_id: str, item_id: str, batch_key: str | None,
                     after_seq: int) -> int:
        """Ledger position, not clock time: immune to timestamp precision/clock skew."""
        q = ("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE org_id=? AND branch_id=?"
             " AND item_id=? AND rowid > ?")
        params: list = [ctx.org_id, branch_id, item_id, after_seq]
        if batch_key is not None:
            q += " AND coalesce(batch_id,'')=?"
            params.append(batch_key)
        return int(self.db.one(q, tuple(params))["q"])

    def variances(self, ctx: Context, sid: str) -> dict:
        ctx.require("inventory.adjust")
        s = self._session(ctx, sid)
        entries = [dict(e) for e in self.db.all(
            "SELECT * FROM count_entries WHERE session_id=? AND superseded_by IS NULL", (sid,))]
        rows: dict[tuple, dict] = {}
        for e in entries:
            k = (e["item_id"], e["batch_key"])
            r = rows.setdefault(k, {"item_id": e["item_id"], "batch_key": e["batch_key"],
                                    "counted": 0, "seq": e["ledger_seq"],
                                    "level": "batch" if e["batch_key"] else "item"})
            r["counted"] += e["qty"]
            r["seq"] = min(r["seq"], e["ledger_seq"])
        # an item counted both by batch and at item level is ambiguous
        items_level = {k[0] for k in rows if k[1] == ""}
        items_batch = {k[0] for k in rows if k[1] != ""}
        out = []
        for (item_id, bk), r in rows.items():
            if item_id in items_level and item_id in items_batch:
                r["problem"] = "mixed_levels"
            if bk:
                system = inventory.batch_qty(self.db, ctx.org_id, s["branch_id"], item_id, bk)
                after = self._moves_after(ctx, s["branch_id"], item_id, bk, r["seq"])
            else:
                system = self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=?"
                                     " AND branch_id=? AND item_id=?",
                                     (ctx.org_id, s["branch_id"], item_id))["q"]
                after = self._moves_after(ctx, s["branch_id"], item_id, None, r["seq"])
            expected_now = r["counted"] + after
            r.update(system_qty=system, moves_after_count=after, counted_now=expected_now,
                     difference=expected_now - system)
            c = self.db.one("SELECT avg_cost FROM item_costs WHERE org_id=? AND branch_id=? AND item_id=?",
                            (ctx.org_id, s["branch_id"], item_id))
            r["value"] = str((Decimal(c["avg_cost"]) * r["difference"]).quantize(Decimal("0.01"))) \
                if c and ctx.can("costs.read") else None
            r["label"] = self.db.one("SELECT coalesce(name_en,name_ar) n FROM items WHERE id=?",
                                     (item_id,))["n"]
            if expected_now < 0:
                r["problem"] = "negative_after_sales"
            out.append(r)
        counted_items = {r["item_id"] for r in out}
        uncounted = []
        for it in self.db.all("SELECT i.* FROM items i WHERE i.org_id=? AND i.status='active'",
                              (ctx.org_id,)):
            if it["id"] in counted_items or not self._in_scope(s, dict(it)):
                continue
            q = self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND"
                            " branch_id=? AND item_id=?", (ctx.org_id, s["branch_id"], it["id"]))["q"]
            if q:
                uncounted.append({"item_id": it["id"], "label": it["name_en"] or it["name_ar"],
                                  "system_qty": q})
        out.sort(key=lambda r: abs(r["difference"]), reverse=True)
        return {"session": s, "lines": out, "uncounted_with_stock": uncounted,
                "summary": {"counted_lines": len(out),
                            "shortage_units": -sum(r["difference"] for r in out if r["difference"] < 0),
                            "excess_units": sum(r["difference"] for r in out if r["difference"] > 0),
                            "uncounted_items": len(uncounted)}}

    def approve(self, ctx: Context, sid: str, *, zero_uncounted: bool = False,
                reason: str | None = None) -> dict:
        ctx.require("inventory.adjust")
        if zero_uncounted and len((reason or "").strip()) < 5:
            raise ValidationFailed("تصفير الأصناف غير المجرودة يحتاج سبب مكتوب")
        with self.db.transaction():
            s = self._session(ctx, sid, open_only=True)
            v = self.variances(ctx, sid)
            bad = [r for r in v["lines"] if r.get("problem")]
            if bad:
                raise Conflict("فيه أصناف محتاجة مراجعة قبل الاعتماد", code="count_problems",
                               details={"lines": bad})
            posted = 0
            for r in v["lines"]:
                diff = r["difference"]
                if diff == 0:
                    continue
                self._post_adjust(ctx, s, r["item_id"], r["batch_key"] or None, diff, sid)
                posted += 1
            if zero_uncounted:
                for u in v["uncounted_with_stock"]:
                    self._post_adjust(ctx, s, u["item_id"], None, -u["system_qty"], sid)
                    posted += 1
            report = {"summary": v["summary"], "adjustment_lines": posted,
                      "zero_uncounted": zero_uncounted, "reason": reason}
            self.db.execute("UPDATE count_sessions SET status='approved', approved_by=?, approved_at=?,"
                            " report=? WHERE id=?",
                            (ctx.user_id, _now(), json.dumps(report, ensure_ascii=False), sid))
            audit.record(self.db, ctx, "count.approve", "count_session", sid, after=report)
        return report

    def _post_adjust(self, ctx: Context, s: dict, item_id: str, batch_id: str | None, diff: int,
                     sid: str) -> None:
        if batch_id or diff > 0:
            inventory.post_movement(self.db, ctx, item_id=item_id, qty_delta=diff, batch_id=batch_id,
                                    movement_type="adjustment", ref_type="count_session", ref_id=sid,
                                    branch_id=s["branch_id"], note="تسوية جرد")
            return
        # item-level shortage: take it from batches, earliest expiry first
        remaining = -diff
        rows = self.db.all(
            "SELECT sb.batch_key, sb.qty FROM stock_balances sb LEFT JOIN batches b"
            " ON b.id = nullif(sb.batch_key,'') WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=?"
            " AND sb.qty > 0 ORDER BY b.expiry_date IS NULL, b.expiry_date",
            (ctx.org_id, s["branch_id"], item_id))
        for r in rows:
            take = min(remaining, r["qty"])
            inventory.post_movement(self.db, ctx, item_id=item_id, qty_delta=-take,
                                    batch_id=r["batch_key"] or None, movement_type="adjustment",
                                    ref_type="count_session", ref_id=sid, branch_id=s["branch_id"],
                                    note="تسوية جرد")
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise Conflict("العجز أكبر من الرصيد المسجل", code="shortage_exceeds_stock")

    def cancel(self, ctx: Context, sid: str) -> None:
        ctx.require("inventory.adjust")
        with self.db.transaction():
            self._session(ctx, sid, open_only=True)
            self.db.execute("UPDATE count_sessions SET status='cancelled' WHERE id=?", (sid,))
            audit.record(self.db, ctx, "count.cancel", "count_session", sid)
