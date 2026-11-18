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
* A manual adjustment or another count's adjustment after that position is a
  book correction, not a physical move: the line is marked
  'adjusted_since_count' and must be recounted before approval.
* Approval (inventory.adjust) posts 'adjustment' movements for the variances.
  Uncounted items are NOT zeroed unless the approver explicitly says so.
"""
from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

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
            raise ValidationFailed("الكمية يجب أن تكون عددًا صحيحًا موجبًا")
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
                    # a sale or receipt since the first scan: the running total mixes units counted before and
                    # after it (a sold unit came back on approval, #324 S14). Count this one again from zero.
                    if self._moved_between(ctx, s["branch_id"], item_id, key, live["ledger_seq"], None):
                        raise Conflict("الصنف ده اتباع أو اتحرك بعد ما بدأت تعدّه؛ عدّه من الأول (عدّ جديد مش إضافة)",
                                       code="moved_since_first_scan")
                    new_qty = live["qty"] + qty
                elif live["counted_by"] != ctx.user_id and not recount:
                    raise Conflict("هذا الصنف جُرد من قبل في المكان نفسه", code="already_counted",
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

    def _moved_between(self, ctx: Context, branch_id: str, item_id: str, batch_key: str | None,
                       after_seq: int, upto_seq: int | None) -> bool:
        """Any stock movement of the item (batch) after one ledger position and up to another (None: now)."""
        q = ("SELECT 1 FROM stock_movements WHERE org_id=? AND branch_id=? AND item_id=? AND rowid > ?")
        params: list = [ctx.org_id, branch_id, item_id, after_seq]
        if upto_seq is not None:
            q += " AND rowid <= ?"
            params.append(upto_seq)
        if batch_key:
            q += " AND coalesce(batch_id,'')=?"
            params.append(batch_key)
        return self.db.one(q + " LIMIT 1", tuple(params)) is not None

    def _adjusted_after(self, ctx: Context, sid: str, branch_id: str, item_id: str,
                        batch_key: str | None, after_seq: int) -> bool:
        """A book correction posted after the entry was counted: a manual adjustment or another
        count's adjustment. It is not a physical move, so "counted + movements after" would apply
        the same difference a second time; the line must be counted again."""
        q = ("SELECT 1 FROM stock_movements WHERE org_id=? AND branch_id=? AND item_id=? AND rowid > ?"
             " AND (ref_type='manual_adjust' OR (ref_type='count_session' AND coalesce(ref_id,'')<>?))")
        params: list = [ctx.org_id, branch_id, item_id, after_seq, sid]
        if batch_key is not None:
            q += " AND coalesce(batch_id,'')=?"
            params.append(batch_key)
        return self.db.one(q + " LIMIT 1", tuple(params)) is not None

    def variances(self, ctx: Context, sid: str) -> dict:
        ctx.require("inventory.adjust")
        s = self._session(ctx, sid)
        entries = [dict(e) for e in self.db.all(
            "SELECT * FROM count_entries WHERE session_id=? AND superseded_by IS NULL", (sid,))]
        rows: dict[tuple, dict] = {}
        for e in entries:
            k = (e["item_id"], e["batch_key"])
            r = rows.setdefault(k, {"item_id": e["item_id"], "batch_key": e["batch_key"],
                                    "counted": 0, "seq": e["ledger_seq"], "last_seq": e["ledger_seq"],
                                    "level": "batch" if e["batch_key"] else "item"})
            r["counted"] += e["qty"]
            r["seq"] = min(r["seq"], e["ledger_seq"])
            r["last_seq"] = max(r["last_seq"], e["ledger_seq"])
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
            r["value"] = str((Decimal(c["avg_cost"]) * r["difference"]).quantize(Decimal("0.01"), ROUND_HALF_UP)) \
                if c and ctx.can("costs.read") else None
            r["label"] = self.db.one("SELECT coalesce(name_en,name_ar) n FROM items WHERE id=?",
                                     (item_id,))["n"]
            if expected_now < 0:
                r["problem"] = "negative_after_sales"
            if r["last_seq"] > r["seq"] and self._moved_between(ctx, s["branch_id"], item_id, bk or None,
                                                                 r["seq"], r["last_seq"]):
                # counted in two places with a sale or receipt in between: which place it touched is unknown,
                # so "counted + moves after the first count" may deduct it twice (#324 S13)
                r["problem"] = "moved_between_counts"
                r["problem_message"] = (f"«{r['label']}»: اتباع أو اتحرك بين عدّه في مكانين؛ أعد عدّه قبل الاعتماد")
            if self._adjusted_after(ctx, sid, s["branch_id"], item_id, bk or None, r["seq"]):
                r["problem"] = "adjusted_since_count"
                r["problem_message"] = (f"«{r['label']}»: تم تعديل رصيده (تسوية يدوية أو جرد آخر) بعد عدّه"
                                        " في هذا الجرد؛ أعد عدّه قبل الاعتماد")
            r.pop("last_seq")
            out.append(r)
        counted_items = {r["item_id"] for r in out}
        uncounted = []
        # one grouped query (not one per item): 20,000 items stay quick on a weak PC
        for it in self.db.all("SELECT i.id, i.name_en, i.name_ar, i.location, sum(sb.qty) q FROM items i"
                              " JOIN stock_balances sb ON sb.item_id=i.id AND sb.org_id=i.org_id AND sb.branch_id=?"
                              " WHERE i.org_id=? AND i.status='active' GROUP BY i.id HAVING sum(sb.qty) <> 0",
                              (s["branch_id"], ctx.org_id)):
            if it["id"] in counted_items or not self._in_scope(s, dict(it)):
                continue
            uncounted.append({"item_id": it["id"], "label": it["name_en"] or it["name_ar"], "system_qty": it["q"]})
        out.sort(key=lambda r: abs(r["difference"]), reverse=True)
        return {"session": s, "lines": out, "uncounted_with_stock": uncounted,
                "summary": {"counted_lines": len(out),
                            "shortage_units": -sum(r["difference"] for r in out if r["difference"] < 0),
                            "excess_units": sum(r["difference"] for r in out if r["difference"] > 0),
                            "uncounted_items": len(uncounted)}}

    def approve(self, ctx: Context, sid: str, *, zero_uncounted: bool = False,
                reason: str | None = None, reasons: dict | None = None,
                shortage_reason: str = "lost", zero_reason: str = "lost",
                uncounted_item_ids: list[str] | None = None) -> dict:
        """reasons: {"item_id|batch_key": reason code} for single lines; others take
        shortage_reason (less than the system) or "found" (more than the system).
        zero_uncounted zeroes exactly the uncounted items the approver was shown (uncounted_item_ids):
        an item received after the preview is refused, never zeroed unseen (#324 S15)."""
        from . import adjust
        ctx.require("inventory.adjust")
        if zero_uncounted is not True and zero_uncounted is not False:
            raise ValidationFailed("قيمة تصفير الأصناف غير المجرودة غير صحيحة")
        if zero_uncounted and len((reason or "").strip()) < 5:
            raise ValidationFailed("تصفير الأصناف غير المجرودة يحتاج إلى سبب مكتوب")
        if zero_uncounted and not isinstance(uncounted_item_ids, list):
            raise ValidationFailed("قائمة الأصناف غير المجرودة المعروضة مطلوبة للتصفير", code="uncounted_list_required")
        reasons = reasons or {}
        for code in [shortage_reason, zero_reason, *reasons.values()]:
            if code not in adjust.REASONS:
                raise ValidationFailed("سبب غير معروف")
        with self.db.transaction():
            s = self._session(ctx, sid, open_only=True)
            v = self.variances(ctx, sid)
            bad = [r for r in v["lines"] if r.get("problem")]
            if bad:
                stale = [r["problem_message"] for r in bad if r["problem"] == "adjusted_since_count"]
                msg = "توجد أصناف تحتاج إلى مراجعة قبل الاعتماد"
                if stale:
                    msg = "لا يمكن اعتماد الجرد: " + stale[0] + (
                        f" (و{len(stale) - 1} صنف آخر بالحالة نفسها)" if len(stale) > 1 else "")
                raise Conflict(msg, code="count_problems",
                               details={"lines": bad})
            posted = 0
            for r in v["lines"]:
                diff = r["difference"]
                if diff == 0:
                    continue
                why = reasons.get(f"{r['item_id']}|{r['batch_key'] or ''}") or ("found" if diff > 0 else shortage_reason)
                adjust.check_reason(why, "تسوية جرد" if why == "other" else None, diff > 0)
                self._post_adjust(ctx, s, r["item_id"], r["batch_key"] or None, diff, sid, why)
                posted += 1
            if zero_uncounted:
                if {u["item_id"] for u in v["uncounted_with_stock"]} != {str(x) for x in uncounted_item_ids}:
                    raise Conflict("الأصناف اللي لم تُجرد اتغيّرت بعد ما عرضت التقرير (صنف اتورد أو اتباع)؛"
                                   " اعرض الفروق تاني قبل التصفير", code="uncounted_changed")
                for u in v["uncounted_with_stock"]:
                    self._post_adjust(ctx, s, u["item_id"], None, -u["system_qty"], sid, zero_reason)
                    posted += 1
            report = {"summary": v["summary"], "adjustment_lines": posted,
                      "zero_uncounted": zero_uncounted, "reason": reason, "shortage_reason": shortage_reason,
                      "zero_reason": zero_reason if zero_uncounted else None, "line_reasons": reasons or None}
            self.db.execute("UPDATE count_sessions SET status='approved', approved_by=?, approved_at=?,"
                            " report=? WHERE id=?",
                            (ctx.user_id, _now(), json.dumps(report, ensure_ascii=False), sid))
            audit.record(self.db, ctx, "count.approve", "count_session", sid, after=report)
        return report

    def _post_adjust(self, ctx: Context, s: dict, item_id: str, batch_id: str | None, diff: int,
                     sid: str, why: str = "lost") -> None:
        from . import adjust
        if diff > 0:
            inventory.post_movement(self.db, ctx, item_id=item_id, qty_delta=diff, batch_id=batch_id,
                                    movement_type="adjustment", ref_type="count_session", ref_id=sid,
                                    branch_id=s["branch_id"], note="تسوية جرد", reason=why,
                                    unit_cost=adjust.avg_cost(self.db, ctx.org_id, s["branch_id"], item_id))
            return
        try:   # shortage: from the counted batch, or the earliest expiry first
            adjust.take(self.db, ctx, item_id, -diff, batch_id=batch_id, reason=why, note="تسوية جرد",
                        ref_type="count_session", ref_id=sid, branch_id=s["branch_id"])
        except Conflict:
            raise Conflict("العجز أكبر من الرصيد المسجل", code="shortage_exceeds_stock")

    def cancel(self, ctx: Context, sid: str) -> None:
        ctx.require("inventory.adjust")
        with self.db.transaction():
            self._session(ctx, sid, open_only=True)
            self.db.execute("UPDATE count_sessions SET status='cancelled' WHERE id=?", (sid,))
            audit.record(self.db, ctx, "count.cancel", "count_session", sid)
