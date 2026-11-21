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
        return self._usage_of(sold, periods, window_days)

    def _usage_of(self, sold: int, periods: list[dict], window_days: int) -> dict:
        """Daily use from the units sold here in the window and the imported periods that reach into it."""
        start = self.today - timedelta(days=window_days)
        hist_qty, hist_days, rates = 0.0, 0, []
        for p in periods:
            ps, pe = date.fromisoformat(p["period_start"]), date.fromisoformat(p["period_end"])
            days = (pe - ps).days + 1
            overlap = (pe - max(ps, start)).days + 1
            frac = overlap / days
            hist_qty += p["qty_base"] * frac
            hist_days += overlap
            rates.append(p["qty_base"] / days)
        # the days the figures cover: the imported periods, then the days after the last of them, which only our
        # own sales cover. Dividing imported + local sales by the imported days alone tripled the daily use of
        # an item with one imported month and two months sold here (#324 S37)
        last = max((date.fromisoformat(p["period_end"]) for p in periods), default=None)
        after = max((self.today - max(last, start)).days - 1, 0) if last else 0
        days = max(hist_days + after, 1) if periods else window_days
        total = hist_qty + sold
        daily = total / days if (periods or sold) else 0.0
        cv = None
        if len(rates) >= 2 and sum(rates) > 0:
            m = sum(rates) / len(rates)
            cv = math.sqrt(sum((r - m) ** 2 for r in rates) / (len(rates) - 1)) / m
        return {"daily": daily, "periods": len(periods), "days_covered": days, "cv": cv,
                "sold_in_system": sold, "history_qty": round(hist_qty, 2), "rates": rates}

    def _all_items(self, ctx: Context, window_days: int) -> dict:
        """What suggest() and stock_health() need for every item, in four questions instead of four per item
        (#253 / #304 B6: 16,000 to 180,000 small queries took 12-25 s and held every other screen meanwhile).
        Same figures as _usage / _sellable / _on_order give for one item."""
        o, b, start = ctx.org_id, ctx.branch_id, (self.today - timedelta(days=window_days)).isoformat()
        sold = {r["item_id"]: -int(r["q"]) for r in self.db.all(
            # the window's sales are read through (org, type, time) and only then grouped. Asked in one plain
            # GROUP BY, SQLite walks an index that starts with the item to save the sort: every movement the
            # pharmacy ever made, instead of the last 90 days
            "WITH w AS MATERIALIZED (SELECT item_id, qty_delta FROM stock_movements WHERE org_id=? AND +branch_id=?"
            " AND movement_type IN ('sale','sale_return') AND created_at >= ?)"
            " SELECT item_id, sum(qty_delta) q FROM w GROUP BY item_id", (o, b, start))}
        periods: dict[str, list] = {}
        for r in self.db.all("SELECT item_id, period_start, period_end, qty_base FROM consumption_history WHERE org_id=?"
                             " AND branch_id=? AND period_end >= ? ORDER BY item_id, period_start", (o, b, start)):
            periods.setdefault(r["item_id"], []).append(dict(r))
        stock = {r["item_id"]: (int(r["q"]), int(r["x"])) for r in self.db.all(
            "SELECT sb.item_id, coalesce(sum(sb.qty),0) q, coalesce(sum(CASE WHEN b.expiry_date IS NOT NULL AND"
            " b.expiry_date<? THEN sb.qty ELSE 0 END),0) x FROM stock_balances sb LEFT JOIN batches b"
            " ON b.id=nullif(sb.batch_key,'') WHERE sb.org_id=? AND sb.branch_id=? GROUP BY sb.item_id",
            (self.today.isoformat(), o, b))}
        on_order = {r["item_id"]: int(r["q"]) for r in self.db.all(
            "SELECT l.item_id, coalesce(sum((l.qty + l.bonus_qty) * i.pack_size),0) q"
            " FROM purchase_invoices p JOIN purchase_invoice_lines l ON l.invoice_id=p.id"
            " JOIN items i ON i.id=l.item_id WHERE p.org_id=? AND p.branch_id=? AND p.status='draft'"
            " AND l.item_id IS NOT NULL AND l.qty IS NOT NULL GROUP BY l.item_id", (o, b))}
        return {"sold": sold, "periods": periods, "stock": stock, "on_order": on_order}

    def _on_hand(self, ctx: Context, item_id: str) -> int:
        return int(self.db.one("SELECT coalesce(sum(qty),0) q FROM stock_balances WHERE org_id=? AND"
                               " branch_id=? AND item_id=?", (ctx.org_id, ctx.branch_id, item_id))["q"])

    def _sellable(self, ctx: Context, item_id: str) -> tuple[int, int]:
        """(units that can still be sold, units past their date). Expired stock is not cover: thirty expired
        units and nothing else used to hide the item from the order list (#324 S38)."""
        r = self.db.one("SELECT coalesce(sum(sb.qty),0) q, coalesce(sum(CASE WHEN b.expiry_date IS NOT NULL AND"
                        " b.expiry_date<? THEN sb.qty ELSE 0 END),0) x FROM stock_balances sb LEFT JOIN batches b"
                        " ON b.id=nullif(sb.batch_key,'') WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=?",
                        (self.today.isoformat(), ctx.org_id, ctx.branch_id, item_id))
        return int(r["q"]) - int(r["x"]), int(r["x"])

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
        d = self._all_items(ctx, window_days)
        for it in self.db.all("SELECT * FROM items WHERE org_id=? AND status='active'", (ctx.org_id,)):
            u = self._usage_of(d["sold"].get(it["id"], 0), d["periods"].get(it["id"], []), window_days)
            total, expired = d["stock"].get(it["id"], (0, 0))
            on_hand, on_order = total - expired, d["on_order"].get(it["id"], 0)
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
            if expired > 0:
                reasons.append(f"منتهي الصلاحية وغير محسوب في الرصيد: {expired} وحدة")
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
                           {"on_order": on_order, "expired": expired, "min_stock": it["min_stock"], "pack_size": it["pack_size"],
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
        d = self._all_items(ctx, slow_days)
        lots: dict[str, list] = {}       # every dated lot in stock, earliest expiry first, in one question
        for b in self.db.all(
                "SELECT sb.item_id, b.batch_no, b.expiry_date, sb.qty FROM stock_balances sb JOIN batches b"
                " ON b.id=sb.batch_key WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0 AND b.expiry_date IS NOT NULL"
                " ORDER BY sb.item_id, b.expiry_date", (ctx.org_id, ctx.branch_id)):
            lots.setdefault(b["item_id"], []).append(b)
        for it in self.db.all("SELECT * FROM items WHERE org_id=? AND status='active'", (ctx.org_id,)):
            on_hand = d["stock"].get(it["id"], (0, 0))[0]
            if on_hand <= 0:
                continue
            u = self._usage_of(d["sold"].get(it["id"], 0), d["periods"].get(it["id"], []), slow_days)
            label = it["name_en"] or it["name_ar"]
            if u["daily"] == 0:
                slow.append({"item_id": it["id"], "label": label, "on_hand": on_hand,
                             "reason": f"لا يوجد استهلاك خلال {slow_days} يوم"})
            elif on_hand / u["daily"] > overstock_days:
                over.append({"item_id": it["id"], "label": label, "on_hand": on_hand,
                             "days_of_cover": round(on_hand / u["daily"]),
                             "reason": f"الرصيد يكفي أكثر من {overstock_days} يوم"})
            spent = 0       # demand already given to an earlier lot: each day's sales empty one lot, not all of them
            for b in lots.get(it["id"], []):
                days_left = (date.fromisoformat(b["expiry_date"]) - self.today).days
                if days_left > expiry_days:
                    continue
                # earliest expiry sells first; what the lots before this one will take is not counted again
                # (two lots of ten expiring in ten days at one a day showed no risk at all, #324 S39)
                sellable = min(max(int(u["daily"] * max(days_left, 0)) - spent, 0), b["qty"])
                spent += sellable
                at_risk = b["qty"] - sellable
                expiring.append({"item_id": it["id"], "label": label, "batch_no": b["batch_no"],
                                 "expiry_date": b["expiry_date"], "days_left": days_left,
                                 "qty": b["qty"], "expected_to_sell": sellable,
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

    def evaluate_offer(self, ctx: Context, item_id: str, *, packs: int, discount_bp: int,
                       expiry_date: str | None = None, cash_available: int | None = None) -> dict:
        """Should we take a big-discount offer? Weighs cover, cash and expiry —
        returns a verdict with reasons; never places an order. Money is whole piasters
        (cost and cash_available), the discount basis points, packs whole packs."""
        from ..sales.service import discount_of
        ctx.require("purchases.read")
        it = self.db.one("SELECT * FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id))
        if not it:
            raise NotFound("الصنف غير موجود")
        if (any(isinstance(v, bool) or not isinstance(v, int) for v in (packs, discount_bp))
                or (cash_available is not None and (isinstance(cash_available, bool) or not isinstance(cash_available, int)))):
            raise ValidationFailed("الكمية والخصم والسيولة أرقام صحيحة")
        if packs <= 0 or not 0 <= discount_bp < 10000 or it["public_price"] is None or (cash_available or 0) < 0:
            raise ValidationFailed("بيانات العرض غير مكتملة (الكمية/الخصم/سعر الجمهور)")
        u = self._usage(ctx, item_id, 90)
        base = packs * it["pack_size"]
        on_hand = self._sellable(ctx, item_id)[0]
        gross = it["public_price"] * packs
        cost = gross - discount_of(gross, discount_bp)
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
            try:
                days_left = (date.fromisoformat(str(expiry_date)) - self.today).days
            except ValueError:
                raise ValidationFailed("تاريخ الصلاحية غير صحيح")
            if u["daily"] and (on_hand + base) / u["daily"] > days_left:
                verdict = "no"
                reasons.append(f"لن تُباع الكمية قبل انتهاء الصلاحية بعد {days_left} يوم")
        if cash_available is not None and cost > cash_available:
            verdict = "no"
            reasons.append("التكلفة أكبر من السيولة المتاحة")
        result = {"verdict": verdict, "cost": cost, "days_of_cover": round(cover, 1) if cover else None,
                  "reasons": reasons}
        with self.db.transaction():
            audit.record(self.db, ctx, "procurement.offer_eval", "item", item_id,
                         after={"packs": packs, "discount_bp": discount_bp, **result})
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
            totals: dict[str, int] = {}      # one figure per item and period: a second row of the same item used
            for item_id, base in matched:   # to overwrite the first while the answer reported their sum (#324 S36)
                totals[item_id] = totals.get(item_id, 0) + base
            for item_id, base in totals.items():
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
