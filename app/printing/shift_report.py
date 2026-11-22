"""Shift-close report (#97): one list of lines for both the browser page and the receipt printer.

The owner asked for the E-Stock drawer layout: a small table الفئة | العدد | المبلغ with one row per
counted note or coin, and the shortage (العجز) and surplus (الزيادة) rows always printed, 0.00 when
there is none. Every amount comes from `CashDrawer.report` (expected cash, counted cash and the
difference saved at close); nothing is re-derived here. Money stays integer piastres and is formatted
with integer arithmetic only.

Line kinds: ("title", text) ("text", text) ("rule",) ("row", label, value) ("total", label, value)
("small", text) ("den_head",) ("den", label, count, amount).
"""
from __future__ import annotations

import html as _html

from ..core import accounts, audit
from ..core.permissions import Context
from ..db import Database
from ..sales.cash import DENOMINATIONS, CashDrawer
from . import escpos

DEN_LABELS = {20000: "200", 10000: "100", 5000: "50", 2000: "20", 1000: "10", 500: "5", 100: "1",
              50: "نصف", 25: "ربع"}


def egp(p: int | None) -> str:
    """Piastres as 1,234.50 (integer arithmetic, no float)."""
    p = int(p or 0)
    sign, a = ("-" if p < 0 else ""), abs(p)
    return f"{sign}{a // 100:,}.{a % 100:02d}"


def shortage_surplus(difference: int | None) -> tuple[int, int]:
    """difference = counted − expected (as saved at close) → (shortage, surplus), both ≥ 0."""
    d = int(difference or 0)
    return (-d, 0) if d < 0 else (0, d)


def denomination_rows(detail: dict | None) -> list[tuple[int, str, int, int]]:
    """(denomination, label, count, amount) for each counted note/coin, largest first."""
    detail = detail or {}
    out = []
    for d in DENOMINATIONS:
        n = int(detail.get(str(d)) or 0)
        if n:
            out.append((d, DEN_LABELS[d], n, d * n))
    return out


def _when(iso: str | None, time_format: str = "12") -> str:
    """The receipt's time format setting, 12-hour with ص/م or 24-hour (#206)."""
    from .settings import fmt_when
    return fmt_when(accounts.parse_iso(iso).astimezone(), time_format) if iso else "—"


def lines(org: str, r: dict, time_format: str = "12") -> list[tuple]:
    k = r["cash_by_kind"]
    _when_tf = lambda iso: _when(iso, time_format)  # noqa: E731
    out: list[tuple] = [("title", org), ("text", "تقرير إغلاق الشيفت"),
                        ("small", f"من {_when_tf(r['opened_at'])} ({r['opened_by_name'] or ''})"),
                        ("small", f"إلى {_when_tf(r['closed_at'])} ({r['closed_by_name'] or ''})")]
    if r.get("closed_at") and r.get("opened_at"):                                          # U2-28
        mins = int((accounts.parse_iso(r["closed_at"]) - accounts.parse_iso(r["opened_at"])).total_seconds() // 60)
        out.append(("small", f"مدة الشيفت: {mins // 60} ساعة و{mins % 60} دقيقة"))
    out += [("rule",), ("row", "عدد الفواتير", str(r["sales_count"])), ("row", "إجمالي المبيعات", egp(r["sales_total"]))]
    if r["discount_total"]:
        out.append(("row", "الخصومات", egp(r["discount_total"])))
    if r["returns_count"]:
        out.append(("row", f"مرتجعات ({r['returns_count']})", egp(r["returns_total"])))
    if r.get("card_refunds"):
        out.append(("row", "منها مرتجع على البطاقة", egp(r["card_refunds"])))
    if r.get("credit_refunds"):
        out.append(("row", "منها مرتجع على حساب العميل", egp(r["credit_refunds"])))
    if r["voided_count"]:
        out.append(("row", "فواتير ملغاة", str(r["voided_count"])))
    out.append(("rule",))
    out.append(("row", "رصيد البداية", egp(r["opening_cash"])))
    for kind, label in (("sale", "مبيعات نقدي"), ("customer_payment", "تحصيل من العملاء (آجل وباقي اتساب رصيد)"), ("refund", "مرتجع نقدي"),
                        ("void", "إلغاء فواتير"), ("payout", "مصروفات من الخزينة"), ("deposit", "إيداع")):
        if k.get(kind):
            out.append(("row", label, egp(k[kind])))
    if r.get("rounding_total"):                         # cash rounding kept in the drawer is not a sale
        out.append(("row", "منها تقريب النقدي (مش مبيعات)", egp(r["rounding_total"])))
    out.append(("total", "المتوقع في الخزينة", egp(r["expected_cash"])))
    dens = denomination_rows(r["counted_detail"])
    if dens:
        out += [("rule",), ("den_head",)] + [("den", lab, str(n), egp(a)) for _, lab, n, a in dens]
    if r["status"] == "closed":
        short, surplus = shortage_surplus(r["difference"])
        out += [("rule",), ("total", "المعدود", egp(r["counted_cash"])),
                ("row", "العجز", egp(short)), ("row", "الزيادة", egp(surplus)),
                ("small", "✓ الخزينة مطابقة" if not (short or surplus) else
                 ("✕ يوجد عجز" if short else "⚠ توجد زيادة"))]
    if r["by_channel"]:
        out.append(("rule",))
        for c in r["by_channel"]:
            out.append(("row", f"{'بطاقة' if c['method'] == 'card' else 'تحويل'} — {c['channel']}", egp(c["amount"])))
    if r["credit_total"]:
        out.append(("row", "آجل", egp(r["credit_total"])))
        for c in r.get("credit_sales") or []:                                              # U2-28
            out.append(("row", f"آجل فاتورة {c['sale_no']} — {c['customer'] or 'بدون اسم'}", egp(c["on_credit"])))
    disc = r.get("discount_by_user") or []
    if disc:
        out.append(("rule",))
        for d in disc:
            out.append(("row", f"خصم {d['name'] or ''} ({d['n']} فاتورة)", egp(d["total"])))
    fl = r.get("float_left")
    if r["status"] == "closed" and fl is not None:                                          # notes kept / moved at close
        keep = (r.get("float_split") or {}).get("keep") or {}
        out += [("rule",), ("total", "الفكة اللي فضلت في الدرج", egp(fl))]
        for dn, n in sorted(keep.items(), key=lambda kv: -int(kv[0])):
            out.append(("row", f"{DEN_LABELS.get(int(dn), dn)} × {n}", egp(int(dn) * int(n))))
        out.append(("total", "اتحوّل للخزنة", egp((r["counted_cash"] or 0) - fl)))
    if r["status"] == "closed" and r["difference"]:
        out.append(("small", "دوّر على سبب الفرق من شاشة الشيفت ← «دوّر على الفرق» (قروش الفواتير، فتح الدرج، المرتجعات)."))
    if r.get("close_note"):
        out.append(("small", f"ملاحظة: {r['close_note']}"))
    # #81: drawer-open commands (the printer cannot confirm the drawer physically opened)
    out += [("rule",), ("row", "فتح الدرج مع البيع النقدي", str(r["drawer_auto_count"])),
            ("row", "فتح الدرج من غير بيع", str(r["drawer_manual_count"]))]
    for d in r["drawer_manual"]:
        note = (" : " + d["note"]) if d.get("note") else ""
        failed = " (فشل)" if d.get("outcome") == "failed" else ""
        out.append(("small", f"{_when_tf(d['at'])} {d.get('by') or ''} — {d.get('reason') or ''}{note}{failed}"))
    out += [("text", "توقيع الكاشير: .................."), ("text", "توقيع المستلم: ..................")]
    return out


# ---------------------------------------------------------------- browser page (fallback)
def page(org: str, r: dict, paper: int, css: str, time_format: str = "12") -> str:
    """css: the <link> tags for receipt.css (and receipt-58.css for 58 mm paper)."""
    e = _html.escape
    body: list[str] = []
    rows: list[str] = []
    den: list[str] = []

    def flush():
        if rows:
            body.append(f"<table>{''.join(rows)}</table>")
            rows.clear()
        if den:
            body.append('<table class="den"><thead><tr><th>الفئة</th><th>العدد</th><th>المبلغ</th></tr></thead>'
                        f"<tbody>{''.join(den)}</tbody></table>")
            den.clear()
    for ln in lines(org, r, time_format):
        kind = ln[0]
        if kind in ("row", "total"):
            if den:
                flush()
            cls = ' class="t"' if kind == "total" else ""
            rows.append(f"<tr{cls}><td>{e(ln[1])}</td><td>{e(ln[2])}</td></tr>")
            continue
        if kind == "den_head":
            flush()
            continue
        if kind == "den":
            den.append(f"<tr><td>{e(ln[1])}</td><td>{e(ln[2])}</td><td>{e(ln[3])}</td></tr>")
            continue
        flush()
        if kind == "title":
            body.append(f"<h1>{e(ln[1])}</h1>")
        elif kind == "text":
            body.append(f"<p>{e(ln[1])}</p>")
        elif kind == "small":
            body.append(f"<p class=\"s\">{e(ln[1])}</p>")
        elif kind == "rule":
            body.append("<hr>")
    flush()
    return (f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>إغلاق الشيفت</title>
{css}</head><body class="w{paper}"><div class="r">{''.join(body)}</div><script src="/static/print.js"></script></body></html>""")


# ---------------------------------------------------------------- receipt printer (ESC/POS image)
def render(org: str, r: dict, width: int = escpos.DOTS_80MM, font_path: str | None = None,
           time_format: str = "12"):
    """The report as one image: text parts through `escpos.render_receipt`, the denomination table drawn
    here in three columns (الفئة on the right, العدد in the middle, المبلغ on the left)."""
    from PIL import Image, ImageDraw, ImageFont
    if not escpos.shaping_available():
        raise escpos.PrinterError("مكتبة تشكيل الحروف العربية (raqm) غير متاحة؛ الإيصال العربي سيظهر مقطعًا")
    fp = font_path or escpos.find_font()
    parts, text, table = [], [], []

    def flush_text():
        if text:
            parts.append(escpos.render_receipt(list(text), width=width, font_path=fp))
            text.clear()

    def flush_table():
        if not table:
            return
        f = ImageFont.truetype(fp, 24 if width >= escpos.DOTS_80MM else 22)
        pad, row_h = 8, 34
        img = Image.new("L", (width, row_h * len(table) + 12), 255)
        d = ImageDraw.Draw(img)
        mid = width * 0.5
        for i, (head, lab, n, amt) in enumerate(table):
            y = 4 + i * row_h
            lw = d.textlength(lab, font=f, direction="rtl")
            d.text((width - pad - lw, y), lab, font=f, fill=0, direction="rtl")
            nw = d.textlength(n, font=f, direction="rtl" if head else "ltr")
            d.text((mid - nw / 2, y), n, font=f, fill=0, direction="rtl" if head else "ltr")
            d.text((pad, y), amt, font=f, fill=0, direction="rtl" if head else "ltr")
            if head:
                for x in range(pad, width - pad, 10):
                    d.line([(x, y + row_h - 2), (x + 5, y + row_h - 2)], fill=0, width=2)
        parts.append(img)
        table.clear()

    for ln in lines(org, r, time_format):
        kind = ln[0]
        if kind == "den_head":
            flush_text()
            table.append((True, "الفئة", "العدد", "المبلغ"))
        elif kind == "den":
            table.append((False, ln[1], ln[2], ln[3]))
        else:
            flush_table()
            if kind == "rule":
                text.append(("rule", "", ""))
            elif kind in ("row", "total"):
                text.append((kind, ln[1], ln[2]))
            else:
                text.append((kind, ln[1], ""))
    flush_table()
    flush_text()
    return escpos.stack(*parts)


def print_report(db: Database, ctx: Context, shift_id: str, target: str | None = None) -> dict:
    """Straight to the receipt printer (no drawer pulse, one copy). Browser printing chosen, or the printer
    failing, raises PrintFailed; the screen then falls back to the browser page."""
    from . import settings as rs
    from .service import PrintFailed, printer_target
    r = CashDrawer(db).report(ctx, shift_id)          # permissions: the manager / owner, or who closed it
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
    cfg = rs.get(db, ctx.org_id)
    dest = target or printer_target(db, ctx.org_id)
    if dest == "none":
        raise PrintFailed("لم يتم اختيار طابعة الإيصال", code="no_printer")
    try:
        img = render(org, r, escpos.DOTS_58MM if cfg["paper"] == 58 else escpos.DOTS_80MM,
                     time_format=cfg["time_format"])
        escpos.send(dest, escpos.receipt_job(img))
    except escpos.PrinterError as e:
        with db.transaction():
            audit.record(db, ctx, "print.shift_report", "cash_shift", shift_id,
                         after={"outcome": "not_printed", "error": str(e)[:200]})
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك") from e
    with db.transaction():
        audit.record(db, ctx, "print.shift_report", "cash_shift", shift_id, after={"outcome": "sent"})
    return {"ok": True}
