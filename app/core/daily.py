"""Once-a-day checks that turn into notifications for the owner and managers:
expired stock still on the shelf, stock expiring within 30 days, and items selling much
faster than usual (may run short). Runs in the background while the program is open."""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, timedelta

from ..db import Database
from . import notify
from .permissions import Context

log = logging.getLogger("pharm.daily")


def _state(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='daily_state'", (org_id,))
    try:
        return json.loads(r["value"]) if r else {}
    except ValueError:
        return {}


def run(db: Database, today: date | None = None) -> int:
    from ..sales.service import local_day_bounds, local_today
    today = today or local_today()
    sent = 0
    from . import retention
    try:
        retention.clean(db)
    except Exception:
        log.exception("cleanup failed")
    for o in db.all("SELECT id FROM organizations WHERE status='active'"):
        st = _state(db, o["id"])
        if st.get("last") == today.isoformat():
            continue
        ctx = Context(user_id="system", org_id=o["id"], branch_id=None)
        with db.transaction():
            expired = db.one("SELECT count(*) c FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key"
                             " WHERE sb.org_id=? AND sb.qty>0 AND b.expiry_date IS NOT NULL AND b.expiry_date<?",
                             (o["id"], today.isoformat()))["c"]
            # expired stock still on the shelf: when the number changes, and again once a week while it stays
            last_exp = st.get("expired_at")
            if expired and (expired != st.get("expired_count") or not last_exp
                            or date.fromisoformat(last_exp) <= today - timedelta(days=7)):
                notify.emit(db, ctx, "expired_stock", "error", f"{expired} تشغيلة انتهت صلاحيتها وما زال لها رصيد",
                            "البيع منها ممنوع تلقائيًا — ارفعها من الرف وسجّلها (مرتجع للمورد أو إعدام)", "report", "expiry")
                st["expired_at"] = today.isoformat()
                sent += 1
            st["expired_count"] = expired
            # batches entering the last 30 days: one notice for the ones that are new since last time
            soon = (today + timedelta(days=30)).isoformat()
            soon_ids = {r["batch_key"] for r in db.all(
                "SELECT DISTINCT sb.batch_key FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key WHERE sb.org_id=?"
                " AND sb.qty>0 AND b.expiry_date>=? AND b.expiry_date<=?", (o["id"], today.isoformat(), soon))}
            new_soon = soon_ids - set(st.get("soon_ids", []))
            if new_soon:
                notify.emit(db, ctx, "expiry_soon", "warning", f"{len(new_soon)} تشغيلة جديدة تنتهي صلاحيتها خلال 30 يومًا",
                            f"كلها الآن {len(soon_ids)} تشغيلة — قدّم بيعها أو أرجعها للمورد قبل انتهائها", "report", "expiry")
                sent += 1
            st["soon_ids"] = sorted(soon_ids)
            st.pop("soon_count", None)
            # selling much faster than usual: last 7 days vs the 8 weeks before
            lo7, _ = local_day_bounds(today - timedelta(days=7))
            lo63, _ = local_day_bounds(today - timedelta(days=63))
            spikes = db.all(
                "WITH s AS (SELECT l.item_id, sum(CASE WHEN x.completed_at>=? THEN l.qty_base ELSE 0 END) recent,"
                "  sum(CASE WHEN x.completed_at<? THEN l.qty_base ELSE 0 END) before FROM sale_lines l JOIN sales x ON x.id=l.sale_id"
                "  WHERE x.org_id=? AND x.status='completed' AND x.completed_at>=? GROUP BY l.item_id)"
                " SELECT s.*, i.name_en, i.name_ar, i.pack_size, coalesce((SELECT sum(qty) FROM stock_balances b WHERE b.item_id=s.item_id"
                "  AND b.org_id=?),0) stock FROM s JOIN items i ON i.id=s.item_id"
                " WHERE s.recent >= 5 AND s.recent >= 3 * (s.before / 8.0) AND s.before > 0", (lo7, lo7, o["id"], lo63, o["id"]))
            notified = st.get("spikes", {})
            for r in spikes:
                last = notified.get(r["item_id"])
                if last and date.fromisoformat(last) > today - timedelta(days=7):
                    continue
                weekly = r["before"] / 8.0
                days_left = r["stock"] / (r["recent"] / 7.0) if r["recent"] else None
                notify.emit(db, ctx, "sales_spike", "warning", f"بيع {r['name_en'] or r['name_ar']} زاد كثيرًا عن المعتاد",
                            f"آخر 7 أيام: {r['recent']} وحدة، والمعتاد نحو {weekly:.0f} في الأسبوع"
                            + (f" · الرصيد يكفي نحو {days_left:.0f} يوم" if days_left is not None else ""), "item", r["item_id"])
                notified[r["item_id"]] = today.isoformat()
                sent += 1
            st["spikes"] = {k: v for k, v in notified.items() if date.fromisoformat(v) > today - timedelta(days=30)}
            from ..finance import schedules
            sent += schedules.remind(db, ctx, today, st)
            st["last"] = today.isoformat()
            db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'daily_state', ?) ON CONFLICT(org_id, key)"
                       " DO UPDATE SET value=excluded.value", (o["id"], json.dumps(st)))
    return sent


class DailyChecks:
    def __init__(self, db: Database):
        self.db, self.stop_event, self.thread = db, threading.Event(), None

    def _loop(self) -> None:
        self.stop_event.wait(60)
        while not self.stop_event.is_set():
            try:
                run(self.db)
            except Exception:
                log.exception("daily checks failed")
            self.stop_event.wait(1800)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="daily-checks", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
