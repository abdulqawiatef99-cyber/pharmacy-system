"""Smart procurement — deterministic, explainable suggestions.

Every suggestion carries the numbers it used, plain-language reasons and a
confidence level. Nothing is ordered automatically; a big discount is never a
reason by itself (evaluate_offer() weighs cover, cash and expiry risk).

Consumption sources: our own 'sale' movements, plus consumption_history
(imported monthly sales from the old system until the Sales phase exists).
Seasonality needs >= 12 months of history and is reported as unavailable
until then rather than guessed.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..core import audit
from ..core.errors import NotFound, ValidationFailed
from ..core.permissions import Context
from ..db import Database, new_id


@dataclass
class Suggestion:
    item_id: str
    label: str
    on_hand: int
    daily_usage: float
    days_of_cover: float | None
    suggested_packs: int
    suggested_base: int
    confidence: str
    reasons: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


class Procurement:
    def __init__(self, db: Database, today: date | None = None):
        self.db = db
        self.today = today or date.today()

    # ------------------------------------------------------------ inputs
    def record_consumption(self, ctx: Context, item_id: str, period_start: str, period_end: str,
                           qty_base: int, source: str = "manual") -> None:
        ctx.require("items.import")
        ps, pe = date.fromisoformat(period_start), date.fromisoformat(period_end)
        if pe < ps or qty_base < 0:
            raise ValidationFailed("فترة أو كمية غير صحيحة")
        if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
            raise NotFound("الصنف غير موجود")
        with self.db.transaction():
            self.db.execute("INSERT INTO consumption_history (id, org_id, branch_id, item_id, period_start,"
                            " period_end, qty_base, source) VALUES (?,?,?,?,?,?,?,?)"
                            " ON CONFLICT(org_id, branch_id, item_id, period_start, period_end, source)"
                            " DO UPDATE SET qty_base=excluded.qty_base",
                            (new_id(), ctx.org_id, ctx.branch_id, item_id, ps.isoformat(), pe.isoformat(),
                             qty_base, source))

    def _usage(self, ctx: Context, item_id: str, window_days: int) -> dict:
        start = self.today - timedelta(days=window_days)
        sold = -int(self.db.one(
            "SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE org_id=? AND branch_id=?"
            " AND item_id=? AND movement_type IN ('sale','sale_return') AND created_at >= ?",
            (ctx.org_id, ctx.branch_id, item_id, start.isoformat()))["q"])
        periods = [dict(r) for r in self.db.all(
            "SELECT period_start, period_end, qty_base FROM consumption_history WHERE org_id=? AND"
            " branch_id=? AND item_id=? AND period_end >= ? ORDER BY period_start",
            (ctx.org_id, ctx.branch_id, item_id, start.isoformat()))]
        hist_qty, hist_days, rates = 0.0, 0, []
        for p in periods:
            ps, pe = date.fromisoformat(p["period_start"]), date.fromisoformat(p["period_end"])
            days = (pe - ps).days + 1
            overlap = (pe - max(ps, start)).days + 1
            frac = overlap / days
            hist_qty += p["qty_base"] * frac
            hist_days += overlap
            rates.append(p["qty_base"] / days)
        days = max(hist_days, 1) if periods else window_days
        total = hist_qty + sold
        daily = total / days if (periods or sold) else 0.0
        cv = None
        if len(rates) >= 2 and sum(rates) > 0:
            m = sum(rates) / len(rates)
            cv = math.sqrt(sum((r - m) ** 2 for r in rates) / (len(rates) - 1)) / m
        return {"daily": daily, "periods": len(periods), "days_covered": days, "cv": cv,
                "sold_in_system": sold, "history_qty": round(hist_qty, 2), "rates": rates}

    def _on_hand(self, ctx: Context, item_id: str) -> int:
        return int(self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND"
                               " branch_id=? AND item_id=?", (ctx.org_id, ctx.branch_id, item_id))["q"])

    def _on_order(self, ctx: Context, item_id: str) -> int:
        """Draft purchase invoices = goods arriving / being entered."""
        r = self.db.one("SELECT coalesce(sum((l.qty + l.bonus_qty) * i.pack_size),0) q"
                        " FROM purchase_invoice_lines l JOIN purchase_invoices p ON p.id=l.invoice_id"
                        " JOIN items i ON i.id=l.item_id WHERE p.org_id=? AND p.branch_id=? AND"
                        " p.status='draft' AND l.item_id=? AND l.qty IS NOT NULL",
                        (ctx.org_id, ctx.branch_id, item_id))
        return int(r["q"])

    # ------------------------------------------------------------ suggestions
    def suggest(self, ctx: Context, *, cover_days: int = 30, lead_days: int = 2,
                safety_days: int = 5, window_days: int = 90, only_needed: bool = True) -> list[dict]:
        ctx.require("purchases.read")
        out: list[Suggestion] = []
        for it in self.db.all("SELECT * FROM items WHERE org_id=? AND status='active'", (ctx.org_id,)):
            u = self._usage(ctx, it["id"], window_days)
            on_hand, on_order = self._on_hand(ctx, it["id"]), self._on_order(ctx, it["id"])
            daily = u["daily"]
            target = daily * (lead_days + cover_days + safety_days)
            need = max(target, it["min_stock"]) - on_hand - on_order
            packs = math.ceil(need / it["pack_size"]) if need > 0 else 0
            cover = (on_hand / daily) if daily > 0 else None
            reasons = []
            if daily > 0:
                reasons.append(f"متوسط الاستهلاك {daily:.2f} وحدة/يوم من {u['days_covered']} يوم")
                reasons.append(f"الرصيد {on_hand} يكفي {cover:.0f} يوم" if cover is not None else "")
            else:
                reasons.append("لا يوجد استهلاك مسجل في الفترة")
            if on_order:
                reasons.append(f"في الطريق/مسودات شراء: {on_order} وحدة")
            if it["min_stock"] and on_hand < it["min_stock"]:
                reasons.append(f"الرصيد أقل من حد الطلب ({it['min_stock']})")
            if packs:
                reasons.append(f"المطلوب يغطي {lead_days} يوم توريد + {cover_days} يوم + {safety_days} يوم أمان")
            if u["periods"] >= 3 and (u["cv"] or 0) < 0.5:
                conf = "high"
            elif u["periods"] >= 2 or u["sold_in_system"] > 0:
                conf = "medium"
            else:
                conf = "low"
                if daily > 0:
                    reasons.append("بيانات قليلة — راجع الكمية بنفسك")
            if u["cv"] is not None and u["cv"] >= 0.5:
                reasons.append("الاستهلاك متذبذب بين الشهور")
            s = Suggestion(it["id"], it["name_en"] or it["name_ar"], on_hand, round(daily, 3),
                           round(cover, 1) if cover is not None else None, packs, packs * it["pack_size"],
                           conf, [r for r in reasons if r],
                           {"on_order": on_order, "min_stock": it["min_stock"], "pack_size": it["pack_size"],
                            "usage": {k: v for k, v in u.items() if k != "rates"},
                            "seasonality": "غير متاح (يحتاج 12 شهر بيانات)"})
            if packs or not only_needed:
                out.append(s)
        out.sort(key=lambda s: (s.days_of_cover if s.days_of_cover is not None else 1e9))
        return [s.__dict__ for s in out]

    # ------------------------------------------------------------ risk lists
    def stock_health(self, ctx: Context, *, slow_days: int = 90, overstock_days: int = 180,
                     expiry_days: int = 180) -> dict:
        ctx.require("inventory.read")
        slow, over, expiring = [], [], []
        for it in self.db.all("SELECT * FROM items WHERE org_id=? AND status='active'", (ctx.org_id,)):
            on_hand = self._on_hand(ctx, it["id"])
            if on_hand <= 0:
                continue
            u = self._usage(ctx, it["id"], slow_days)
            label = it["name_en"] or it["name_ar"]
            if u["daily"] == 0:
                slow.append({"item_id": it["id"], "label": label, "on_hand": on_hand,
                             "reason": f"لا يوجد استهلاك خلال {slow_days} يوم"})
            elif on_hand / u["daily"] > overstock_days:
                over.append({"item_id": it["id"], "label": label, "on_hand": on_hand,
                             "days_of_cover": round(on_hand / u["daily"]),
                             "reason": f"الرصيد يكفي أكثر من {overstock_days} يوم"})
            for b in self.db.all(
                    "SELECT b.batch_no, b.expiry_date, sb.qty FROM stock_balances sb JOIN batches b"
                    " ON b.id=sb.batch_key WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=?"
                    " AND sb.qty>0 AND b.expiry_date IS NOT NULL ORDER BY b.expiry_date",
                    (ctx.org_id, ctx.branch_id, it["id"])):
                days_left = (date.fromisoformat(b["expiry_date"]) - self.today).days
                if days_left > expiry_days:
                    continue
                sellable = int(u["daily"] * max(days_left, 0))
                at_risk = max(0, b["qty"] - sellable)
                expiring.append({"item_id": it["id"], "label": label, "batch_no": b["batch_no"],
                                 "expiry_date": b["expiry_date"], "days_left": days_left,
                                 "qty": b["qty"], "expected_to_sell": min(sellable, b["qty"]),
                                 "units_at_risk": at_risk,
                                 "action": "مرتجع للموزع/تبديل/عرض" if at_risk else "متوقع يتباع قبل الانتهاء"})
        expiring.sort(key=lambda e: (e["days_left"], -e["units_at_risk"]))
        return {"slow_movers": slow, "overstock": over, "near_expiry": expiring}

    # ------------------------------------------------------------ suppliers
    def supplier_costs(self, ctx: Context, item_id: str) -> list[dict]:
        """Effective cost per base unit by supplier from approved invoices
        (bonus included as free units; invoice-level discount allocated)."""
        ctx.require("costs.read")
        if not self.db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
            raise NotFound("الصنف غير موجود")
        rows = self.db.all(
            "SELECT p.supplier_id, s.name, p.approved_at, m.unit_cost, m.qty_delta"
            " FROM stock_movements m JOIN purchase_invoices p ON p.id=m.ref_id"
            " LEFT JOIN suppliers s ON s.id=p.supplier_id"
            " WHERE m.org_id=? AND m.item_id=? AND m.movement_type='purchase' AND"
            " m.ref_type='purchase_invoice' AND p.status='approved' ORDER BY p.approved_at DESC",
            (ctx.org_id, item_id))
        best: dict[str, dict] = {}
        for r in rows:
            k = r["supplier_id"] or "?"
            if k not in best:   # latest purchase per supplier
                best[k] = {"supplier_id": r["supplier_id"], "supplier": r["name"],
                           "last_unit_cost": r["unit_cost"], "last_purchase": r["approved_at"],
                           "purchases": 0}
            best[k]["purchases"] += 1
        return sorted(best.values(), key=lambda x: Decimal(x["last_unit_cost"]))

    def evaluate_offer(self, ctx: Context, item_id: str, *, packs: int, discount_pct: float,
                       expiry_date: str | None = None, cash_available: float | None = None) -> dict:
        """Should we take a big-discount offer? Weighs cover, cash and expiry —
        returns a verdict with reasons; never places an order."""
        ctx.require("purchases.read")
        it = self.db.one("SELECT * FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
        if not it:
            raise NotFound("الصنف غير موجود")
        if packs <= 0 or not 0 <= discount_pct < 100 or it["public_price"] is None:
            raise ValidationFailed("بيانات العرض غير مكتملة (الكمية/الخصم/سعر الجمهور)")
        u = self._usage(ctx, item_id, 90)
        base = packs * it["pack_size"]
        on_hand = self._on_hand(ctx, item_id)
        cost = it["public_price"] / 100 * packs * (1 - discount_pct / 100)
        reasons, verdict = [], "ok"
        if u["daily"] == 0:
            verdict = "no"
            reasons.append("لا يوجد استهلاك مسجل — خطر ركود")
            cover = None
        else:
            cover = (on_hand + base) / u["daily"]
            reasons.append(f"الكمية مع الرصيد تكفي {cover:.0f} يوم")
            if cover > 120:
                verdict = "caution"
                reasons.append("تغطية أكثر من 4 شهور تحبس سيولة")
        if expiry_date:
            days_left = (date.fromisoformat(expiry_date) - self.today).days
            if u["daily"] and (on_hand + base) / u["daily"] > days_left:
                verdict = "no"
                reasons.append(f"لن تُباع الكمية قبل انتهاء الصلاحية بعد {days_left} يوم")
        if cash_available is not None and cost > cash_available:
            verdict = "no"
            reasons.append("التكلفة أكبر من السيولة المتاحة")
        result = {"verdict": verdict, "cost": round(cost, 2), "days_of_cover": round(cover, 1) if cover else None,
                  "reasons": reasons}
        with self.db.transaction():
            audit.record(self.db, ctx, "procurement.offer_eval", "item", item_id,
                         after={"packs": packs, "discount_pct": discount_pct, **result})
        return result


# ---------------------------------------------------------------- import
def import_consumption(db: Database, ctx: Context, data: bytes, filename: str, *,
                       period_start: str, period_end: str, qty_unit: str = "pack",
                       dry_run: bool = True) -> dict:
    """Monthly sales report from the old system -> consumption_history.
    Items are matched by code or barcode only (never by name similarity);
    unmatched rows are reported, not guessed. dry_run=True writes nothing."""
    from ..catalog.normalize import normalize_barcode
    from ..dataimport.service import _detect, _read
    from ..purchasing.model import parse_decimal
    ctx.require("items.import")
    date.fromisoformat(period_start)
    date.fromisoformat(period_end)
    rows = _read(data, filename)
    found = _detect(rows)
    if not found or "qty" not in found[1].values():
        raise ValidationFailed("لم أجد أعمدة الكود/الاسم والكمية في الملف", code="mapping_required")
    hi, cmap = found
    col = {f: j for j, f in cmap.items()}
    matched, unmatched, problems = [], [], []
    for n, r in enumerate(rows[hi + 1:], start=hi + 2):
        get = lambda f: r[col[f]] if f in col and col[f] < len(r) else None  # noqa: E731
        code, bc, qty_raw = get("code"), get("barcode"), get("qty")
        if all(v in (None, "") for v in (code, bc, get("name_en"), qty_raw)):
            continue
        item = None
        if code not in (None, ""):
            c = str(int(code)) if isinstance(code, float) and code == int(code) else str(code).strip()
            item = db.one("SELECT id, pack_size FROM items WHERE org_id=? AND code=?", (ctx.org_id, c))
        if not item and bc not in (None, ""):
            b = normalize_barcode(str(int(bc)) if isinstance(bc, float) else str(bc))
            item = db.one("SELECT i.id, i.pack_size FROM item_barcodes x JOIN items i ON i.id=x.item_id"
                          " WHERE x.org_id=? AND x.barcode=?", (ctx.org_id, b))
        q = parse_decimal(qty_raw)
        if not item:
            unmatched.append({"row": n, "code": code, "name": get("name_en")})
            continue
        if q is None or q < 0:
            problems.append({"row": n, "issue": "invalid_qty", "value": str(qty_raw)})
            continue
        base = q * item["pack_size"] if qty_unit == "pack" else q
        if base != base.to_integral_value():
            problems.append({"row": n, "issue": "fractional_units", "value": str(q)})
            continue
        matched.append((item["id"], int(base)))
    if not dry_run:
        with db.transaction():
            p = Procurement(db)
            for item_id, base in matched:
                p.record_consumption(ctx, item_id, period_start, period_end, base,
                                     source=f"import:{period_start}")
            audit.record(db, ctx, "procurement.consumption_import", "consumption", period_start,
                         after={"file": filename, "rows": len(matched), "unmatched": len(unmatched)})
    return {"matched_rows": len(matched), "unmatched": unmatched, "problems": problems,
            "total_base_units": sum(b for _, b in matched), "applied": not dry_run}


def suggestions_xlsx(rows: list[dict], db: Database, ctx: Context) -> bytes:
    """Order sheet (name, code, barcode, packs) — e.g. to upload to a B2B ordering app."""
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "order"
    ws.append(["الصنف", "الكود", "الباركود", "الكمية (علبة)", "الرصيد (وحدة)", "يكفي (يوم)", "الثقة"])
    for r in rows:
        it = db.one("SELECT code FROM items WHERE id=? AND org_id=?", (r["item_id"], ctx.org_id))
        bc = db.one("SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=? LIMIT 1",
                    (ctx.org_id, r["item_id"]))
        ws.append([r["label"], it["code"] if it else None, bc["barcode"] if bc else None,
                   r["suggested_packs"], r["on_hand"], r["days_of_cover"], r["confidence"]])
    from ..core.xlsx_safe import as_text
    as_text(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
