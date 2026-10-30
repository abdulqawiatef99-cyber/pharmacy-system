"""Server-side printing for the pharmacy node (printer attached to the PC)."""
from __future__ import annotations

import logging
import os

from ..core import accounts, audit
from ..core.errors import DomainError, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales.service import Sales
from . import escpos

log = logging.getLogger("pharm.print")


class PrintFailed(DomainError):
    status = 502
    code = "print_failed"


def printer_target(db: Database | None = None, org_id: str | None = None) -> str:
    """The receipt printer: chosen in the settings screen ("browser" = browser printing on purpose),
    else PHARM_PRINTER (older installs)."""
    if db is not None and org_id:
        from . import settings as rs
        chosen = rs.get(db, org_id).get("receipt_target")
        if chosen == "browser":
            return "none"
        if chosen:
            return chosen
    return os.environ.get("PHARM_PRINTER", "none").strip() or "none"


def print_receipt(db: Database, ctx: Context, sale_id: str, *, open_drawer: bool = False,
                  target: str | None = None) -> dict:
    """open_drawer is the automatic checkout pulse only (checkout_devices); a reprint never opens the drawer."""
    ctx.require("sales.read")
    sale = Sales(db).get(ctx, sale_id)
    if sale["status"] != "completed":
        raise ValidationFailed("الإيصال متاح للفواتير المكتملة فقط")
    if open_drawer:
        ctx.require("sales.create")
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
    from . import settings as rs
    cfg = rs.get(db, ctx.org_id)
    when = rs.fmt_when(accounts.parse_iso(sale["completed_at"]).astimezone(), cfg["time_format"])
    dest = target or printer_target(db, ctx.org_id)
    if dest == "none":                      # browser printing chosen: not an error to show the cashier
        raise PrintFailed("لم يتم اختيار طابعة الإيصال", code="no_printer")
    rendered = False
    try:
        img = escpos.render_receipt(escpos.receipt_lines(org, sale, when, cfg),
                                    width=escpos.DOTS_58MM if cfg["paper"] == 58 else escpos.DOTS_80MM)
        rendered = True
        escpos.send(dest, escpos.receipt_job(img, open_drawer, cfg["drawer_timing"], cfg["copies"]))
    except escpos.PrinterError as e:
        # The receipt could not be drawn here (no Arabic shaping / font) but the printer may be fine:
        # the cash drawer still opens, and the browser prints the receipt instead.
        drawer = open_drawer and not rendered and _kick(db, ctx, dest, sale_id)
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك",
                          details={"drawer_opened": bool(drawer)}) from e
    with db.transaction():
        audit.record(db, ctx, "print.receipt", "sale", sale_id, after={"drawer": open_drawer})
        if open_drawer:
            audit.record(db, ctx, "cash.drawer_auto", "sale", sale_id, after={"with_receipt": True, "outcome": "sent"})
    return {"ok": True}


def _kick(db: Database, ctx: Context, dest: str, sale_id: str) -> bool:
    """The automatic checkout pulse alone (no receipt, no feed, no cut)."""
    try:
        escpos.send(dest, escpos.INIT + escpos.DRAWER_KICK)
    except escpos.PrinterError:
        return False
    with db.transaction():
        audit.record(db, ctx, "cash.drawer_auto", "sale", sale_id, after={"with_receipt": False, "outcome": "sent"})
    return True


def checkout_devices(db: Database, ctx: Context, sale: dict, receipt: bool) -> dict:
    """After a sale is committed (#81), once: the call site skips replays of the same request_id.
    The drawer opens on any cash part of a store sale, with or without a receipt; a delivery sale prints
    one receipt and never opens the drawer. A failure here never touches the saved sale.
    States: receipt none|sent|browser|failed; drawer none|sent|failed|unsupported (browser printing)."""
    from . import settings as rs
    ctx.require("sales.create")            # outside the try below: refused, never reported as a printer failure
    cfg = rs.get(db, ctx.org_id)
    delivery = sale.get("kind") == "delivery"
    want_drawer = bool(cfg["drawer_on_cash"]) and (sale.get("paid_cash") or 0) > 0 and not delivery
    want_receipt = bool(receipt) or delivery
    out = {"receipt": "none", "drawer": "none", "message": None}
    dest = printer_target(db, ctx.org_id)
    if dest == "none":
        out["receipt"] = "browser" if want_receipt else "none"
        out["drawer"] = "unsupported" if want_drawer else "none"
        return out
    try:
        if want_receipt:
            try:
                print_receipt(db, ctx, sale["id"], open_drawer=want_drawer, target=dest)
                out["receipt"], out["drawer"] = "sent", "sent" if want_drawer else "none"
            except PrintFailed as e:
                out["receipt"], out["message"] = "failed", e.message
                if want_drawer:
                    out["drawer"] = "sent" if (e.details or {}).get("drawer_opened") else "failed"
        elif want_drawer:
            out["drawer"] = "sent" if _kick(db, ctx, dest, sale["id"]) else "failed"
    except Exception as e:  # noqa: BLE001 — the sale is saved; report the device problem, never fail the sale
        log.exception("checkout devices failed for sale %s", sale.get("id"))
        out["message"] = str(e)
        out["receipt"] = "failed" if want_receipt else "none"
        out["drawer"] = "failed" if want_drawer and out["drawer"] == "none" else out["drawer"]
    if want_drawer and out["drawer"] == "failed":
        with db.transaction():
            audit.record(db, ctx, "cash.drawer_auto", "sale", sale["id"], after={"outcome": "failed"})
    return out


DRAWER_REASONS = {"change": "فكة", "cash_check": "مراجعة النقدية", "other": "أخرى"}


def open_drawer(db: Database, ctx: Context, reason: str, target: str | None = None, note: str = "") -> dict:
    """Opening the drawer without a sale is sensitive: permission + reason + audit (the shift, the outcome).
    reason: change | cash_check | other (+ note), or free text from older screens / the settings test."""
    ctx.require("cash.manage")
    reason, note = (reason or "").strip(), (note or "").strip()
    if reason in DRAWER_REASONS:
        if reason == "other" and len(note) < 3:
            raise ValidationFailed("اكتب سبب فتح الدرج")
        label = DRAWER_REASONS[reason]
    elif len(reason) < 3:
        raise ValidationFailed("اكتب سبب فتح الخزينة")
    else:
        label = reason
    from ..sales import cash
    shift = cash.open_shift_id(db, ctx)
    dest = target or printer_target(db, ctx.org_id)
    error = None
    try:
        if dest == "none":
            raise escpos.PrinterError("الطباعة من المتصفح لا تستطيع فتح الدرج — اختر طابعة الإيصال المباشرة في الإعدادات")
        escpos.send(dest, escpos.INIT + escpos.DRAWER_KICK)
    except escpos.PrinterError as e:
        error = str(e)
    with db.transaction():
        audit.record(db, ctx, "cash.drawer_open", "cash_shift", shift,
                     after={"reason": label, "note": note[:200], "outcome": "failed" if error else "sent"})
    if error:
        raise PrintFailed(error)
    return {"ok": True, "sent": True}


def printers(db: Database, ctx: Context) -> dict:
    """What the settings screen offers: the Windows printers on this PC and whether Arabic can be drawn."""
    ctx.require("settings.manage")
    return {"printers": escpos.windows_printers(), "shaping": escpos.shaping_available(),
            "env": os.environ.get("PHARM_PRINTER", "none").strip() or "none"}


def print_test(db: Database, ctx: Context, target: str | None = None) -> dict:
    """A short Arabic test receipt on the printer being set up (no sale, no drawer)."""
    from . import settings as rs
    ctx.require("settings.manage")
    dest = rs.check_target(target, allow_browser=True) if target else printer_target(db, ctx.org_id)
    dest = "none" if dest == "browser" else dest
    try:
        img = escpos.render_receipt([("title", "اختبار الطابعة", ""), ("row", "سطر عربي", "12.50"),
                                     ("small", "إذا ظهر النص مقروءًا فالطباعة سليمة", "")])
        escpos.send(dest, escpos.receipt_job(img))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e)) from e
    with db.transaction():
        audit.record(db, ctx, "print.test", "printer", None, after={"target": dest})
    return {"ok": True}


def label_data(db: Database, ctx: Context, it, cfg: dict) -> dict:
    """What one item's label can show (#84): names per the receipt setting, the barcode, internal code, public
    price, the nearest expiry among the batches in stock here, and the pharmacy name / phone."""
    from . import labels as lb, settings as rs
    codes = [r["barcode"] for r in db.all("SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=?",
                                          (ctx.org_id, it["id"]))]
    exp = db.one("SELECT min(b.expiry_date) e FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key"
                 " WHERE sb.org_id=? AND sb.branch_id=? AND sb.item_id=? AND sb.qty>0 AND b.expiry_date IS NOT NULL",
                 (ctx.org_id, ctx.branch_id, it["id"]))["e"]
    pharmacy, phone = rs.label_identity(db, ctx.org_id, cfg)
    name = (it["name_ar"] or it["name_en"]) if cfg["names"] == "ar" else (it["name_en"] or it["name_ar"])
    return {"name": name or "", "value": str(lb.label_value(dict(it), codes)), "code": it["code"] or "",
            "price": f"{it['public_price'] / 100:,.2f} ج" if it["public_price"] is not None else "",
            "expiry": f"{exp[5:7]}/{exp[2:4]}" if exp else "", "pharmacy": pharmacy, "phone": phone}


def label_size(cfg: dict, size: str | None = None) -> tuple[float, float]:
    from . import labels as lb, settings as rs
    if cfg["label_lang"] == "a4":                  # one cell of the A4 sheet grid
        cols, rows = rs.a4_grid(cfg["label_a4"])
        return round(210 / cols, 1), round(297 / rows, 1)
    try:
        return lb.parse_size(size or cfg["label_size"])
    except escpos.PrinterError as e:
        raise ValidationFailed(str(e)) from e


def label_preview(db: Database, ctx: Context, data: dict | None = None) -> tuple[bytes, list[str]]:
    """The label as it will print, for the settings screen (sample item, the saved fields and size)."""
    import io
    from . import labels as lb, settings as rs
    ctx.require("settings.manage")
    saved = rs.get(db, ctx.org_id)
    cfg = {**saved, **{k: v for k, v in (data or {}).items() if k in ("label_pharmacy", "label_phone", "label_lang", "label_a4")}}
    cfg["label_dpi"] = (data or {}).get("label_dpi") if (data or {}).get("label_dpi") in lb.DPIS else saved["label_dpi"]
    cfg["label_lang"] = cfg["label_lang"] if cfg["label_lang"] in lb.LANGS else saved["label_lang"]
    cfg["label_pharmacy"], cfg["label_phone"] = str(cfg["label_pharmacy"] or "")[:40], str(cfg["label_phone"] or "")[:40]
    if data and "label_size" in data:
        cfg["label_size"] = rs.label_size_ok(data["label_size"])
    if data and isinstance(data.get("label_fields"), dict):
        cfg["label_fields"] = {**lb.DEFAULT_FIELDS, **{k: bool(v) for k, v in data["label_fields"].items() if k in lb.DEFAULT_FIELDS}}
    w, h = label_size(cfg)
    pharmacy, phone = rs.label_identity(db, ctx.org_id, cfg)
    sample = {"name": "Panadol Extra 24 Tab", "value": "6221025012343", "code": "8130", "price": "40.00 ج",
              "expiry": "10/27", "pharmacy": pharmacy, "phone": phone}
    try:
        img, dropped = lb.render_label(sample, w, h, cfg["label_dpi"], cfg["label_fields"])
    except escpos.PrinterError as e:
        raise ValidationFailed(str(e), code="label_too_small") from e
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), dropped


def print_labels(db: Database, ctx: Context, wanted: list[tuple[str, int]], *, size: str | None = None,
                 target: str | None = None) -> dict:
    """Barcode labels straight to the label printer (TSPL for Xprinter/TSC, ZPL for Zebra)."""
    from . import labels as lb, settings as rs
    ctx.require("items.read")
    cfg = rs.get(db, ctx.org_id)
    if cfg["label_lang"] in ("browser", "a4"):
        raise ValidationFailed("طابعة الملصقات مضبوطة على الطباعة من المتصفح", code="labels_in_browser")
    w, h = label_size(cfg, size)
    total = sum(n for _, n in wanted)
    if not wanted or total > lb.MAX_LABELS or any(not isinstance(n, int) or n < 1 for _, n in wanted):
        raise ValidationFailed(f"عدد الملصقات من 1 إلى {lb.MAX_LABELS}")
    imgs, dropped = [], set()
    try:
        for iid, n in wanted:
            it = db.one("SELECT * FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
            if not it or (it["hidden"] and not ctx.can("items.hidden")):
                continue
            img, gone = lb.render_label(label_data(db, ctx, it, cfg), w, h, cfg["label_dpi"], cfg["label_fields"])
            imgs.append((img, n))
            dropped.update(gone)
        if not imgs:
            raise ValidationFailed("اختر الأصناف")
        escpos.send(target or cfg["label_target"] or "none",
                    lb.job(cfg["label_lang"], imgs, w, h, cfg["label_gap"]))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك") from e
    with db.transaction():
        audit.record(db, ctx, "print.labels", "item", None,
                     after={"labels": total, "size": f"{w:g}x{h:g}", "lang": cfg["label_lang"]})
    return {"ok": True, "labels": total, "dropped": sorted(dropped)}
