"""Server-side printing for the pharmacy node (printer attached to the PC)."""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from datetime import datetime

from PIL import Image

from ..core import accounts, audit
from ..core.errors import Conflict, DomainError, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales import registers
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


def _receipt_destination(db: Database, ctx: Context, register) -> str:
    if not register:
        return "none"
    target = register["receipt_printer"]
    return "none" if target == "browser" else target or printer_target(db, ctx.org_id)


def _drawer_kick_available(db: Database, ctx: Context, register) -> bool:
    if register["id"] == 1:
        return register["drawer_kick"] != 0
    return bool(register["drawer_kick"]) and registers.independent_drawer_printer(
        db, ctx, register["id"], register["receipt_printer"])


def label_config(db: Database, ctx: Context, cfg: dict | None = None) -> dict:
    from . import settings as rs

    cfg = rs.get(db, ctx.org_id) if cfg is None else dict(cfg)
    register = registers.current(db, ctx)
    if not register and cfg["label_lang"] not in ("browser", "a4"):
        cfg["label_lang"] = "browser"
    elif register and register["label_printer"]:
        target = register["label_printer"]
        cfg["label_target"] = target
        if cfg["label_lang"] in ("browser", "a4") or (cfg["label_lang"] == "driver" and target.startswith("tcp:")):
            cfg["label_lang"] = "driver" if target.startswith("win:") else "tspl"
    return cfg


def print_receipt(db: Database, ctx: Context, sale_id: str, *, open_drawer: bool = False,
                  target: str | None = None, given: int = 0) -> dict:
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
    dest = target or _receipt_destination(db, ctx, registers.current(db, ctx))
    if dest == "none":                      # browser printing chosen: not an error to show the cashier
        raise PrintFailed("لم تُختر طابعة الإيصال (البرنامج ← طابعة الإيصال والدرج)", code="no_printer")
    rendered = False
    try:
        seller = cashier = ""
        if cfg["show_cashier"]:
            staff = db.one(
                "SELECT seller.full_name seller, cashier.full_name cashier FROM sales s"
                " LEFT JOIN users seller ON seller.id=s.created_by"
                " LEFT JOIN users cashier ON cashier.id=("
                "   SELECT a.user_id FROM audit_log a WHERE a.org_id=s.org_id"
                "     AND a.entity_type='sale' AND a.entity_id=s.id AND a.action='sale.complete'"
                "   ORDER BY a.at DESC, a.id DESC LIMIT 1)"
                " WHERE s.org_id=? AND s.id=?", (ctx.org_id, sale_id))
            if staff:
                seller = staff["seller"] or ""
                cashier = staff["cashier"] or ""
        from . import logo
        from ..sales.service import return_window_days
        png = logo.get_png(db, ctx.org_id) if cfg["has_logo"] else None
        img = receipt_image(org, sale, when, cfg, logo_png=png, seller=seller, cashier=cashier,
                            given=given, return_days=return_window_days(db, ctx.org_id))
        rendered = True
        escpos.send(dest, escpos.receipt_job(img, open_drawer, cfg["drawer_timing"], cfg["copies"]))
    except escpos.PrinterError as e:
        # The receipt could not be drawn here (no Arabic shaping / font) but the printer may be fine:
        # the cash drawer still opens, and the browser prints the receipt instead.
        drawer = open_drawer and not rendered and _kick(db, ctx, dest, sale_id)
        if rendered:            # #98: the printer did not take the receipt; audit what really happened
            with db.transaction():
                audit.record(db, ctx, "print.receipt", "sale", sale_id,
                             after={"drawer": open_drawer, "outcome": "not_printed", "error": str(e)[:200]})
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك",
                          details={"drawer_opened": bool(drawer)}) from e
    with db.transaction():
        audit.record(db, ctx, "print.receipt", "sale", sale_id, after={"drawer": open_drawer, "outcome": "sent"})
        if open_drawer:
            audit.record(db, ctx, "cash.drawer_auto", "sale", sale_id, after={"with_receipt": True, "outcome": "sent"})
    return {"ok": True}


def receipt_image(org: str, sale: dict, when: str, cfg: dict, *, logo_png: bytes | None = None,
                  seller: str = "", cashier: str = "", given: int = 0,
                  return_days: int | None = None) -> Image.Image:
    """Compose the bitmap used for both printing and settings preview."""
    from . import qrlinks

    width = escpos.DOTS_58MM if cfg["paper"] == 58 else escpos.DOTS_80MM
    try:
        header = escpos.receipt_header(width, logo_png)
    except Exception:  # noqa: BLE001 — a broken logo never stops the receipt (or the drawer kick)
        log.exception("Could not read the receipt logo")
        header = None
    codes = []
    for prefix in ("qr", "qr2"):
        text = cfg[f"{prefix}_text"]
        if not text:
            continue
        try:
            codes.append(escpos.receipt_footer_qr(width, text, qrlinks.caption(text, cfg[f"{prefix}_caption"])))
        except Exception:  # noqa: BLE001 — a broken QR never stops the receipt
            log.exception("Could not render receipt QR code")
    lines = escpos.receipt_lines(org, sale, when, cfg, seller=seller, cashier=cashier,
                                 given=given, return_days=return_days)
    return escpos.render_receipt(lines, width, template=cfg["template"], header=header, footer_codes=tuple(codes))


def receipt_preview(db: Database, ctx: Context, cfg: dict, logo_png: bytes | None) -> str:
    """Preview the draft settings with a synthetic sale and no device or database writes."""
    from . import settings as rs
    from ..sales.service import return_window_days

    ctx.require("settings.manage")
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
    sample = {
        "sale_no": 12048, "kind": "store", "subtotal": 6250, "discount_total": 700,
        "total": 5550, "rounding": -50, "paid_cash": 5500,      # UX-26: a rounding, so its toggle shows in the preview
        "customer": {"name": "عميل تجريبي", "estock_code": 35, "phone": "01000000000"},
        "lines": [
            {"item": {"name": "Sample Relief 24 Tablets", "name_ar": "أقراص تجريبية",
                      "name_en": "Sample Relief 24 Tablets", "pack_size": 4, "base_unit": "شريط"},
             "qty_base": 4, "line_total": 4300, "discount_bp": 1000, "discount": 700, "invoice_share": 200,
             "official_prices": [{"pack": True, "qty": 1, "price": 5000, "gross": 5000}]},
            {"item": {"name": "Sample Vitamin C 500 mg", "name_ar": "فيتامين سي تجريبي",
                      "name_en": "Sample Vitamin C 500 mg", "pack_size": 4, "base_unit": "شريط"},
             "qty_base": 1, "line_total": 1250,
             "official_prices": [{"pack": False, "qty": 1, "price": 1250, "gross": 1250}]},
        ],
    }
    when = rs.fmt_when(datetime(2026, 11, 23, 14, 5), cfg["time_format"])
    try:
        image = receipt_image(org, sample, when, cfg, logo_png=logo_png, seller="بائع تجريبي",
                              cashier="أمين صندوق تجريبي", given=6000, return_days=return_window_days(db, ctx.org_id))
    except escpos.PrinterError as exc:
        raise ValidationFailed(str(exc)) from exc
    buf = io.BytesIO()
    image.point(lambda p: 255 if p > escpos.THRESHOLD else 0, mode="1").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _kick(db: Database, ctx: Context, dest: str, sale_id: str) -> bool:
    """The automatic checkout pulse alone (no receipt, no feed, no cut)."""
    try:
        escpos.send(dest, escpos.INIT + escpos.DRAWER_KICK)
    except escpos.PrinterError:
        return False
    with db.transaction():
        audit.record(db, ctx, "cash.drawer_auto", "sale", sale_id, after={"with_receipt": False, "outcome": "sent"})
    return True


def checkout_devices(db: Database, ctx: Context, sale: dict, receipt: bool, given: int = 0, *,
                     drawer_first: bool = False) -> dict:
    """After a sale is committed (#81), once: the call site skips replays of the same request_id.
    The drawer opens on any cash part of a store sale, with or without a receipt; a delivery sale prints
    one receipt and never opens the drawer. A failure here never touches the saved sale.
    States: receipt none|sent|browser|failed; drawer none|sent|failed|unsupported (browser printing)."""
    from . import settings as rs
    ctx.require("sales.create")            # outside the try below: refused, never reported as a printer failure
    cfg = rs.get(db, ctx.org_id)
    delivery = sale.get("kind") == "delivery"
    register = registers.current(db, ctx)
    want_drawer = (
        bool(register) and (sale.get("paid_cash") or 0) > 0 and not delivery
        and (register["drawer_kick"] is not None or cfg["drawer_on_cash"])
        and _drawer_kick_available(db, ctx, register)
    )
    want_receipt = bool(receipt) or delivery
    out = {"receipt": "none", "drawer": "none", "message": None}
    dest = _receipt_destination(db, ctx, register)
    if dest == "none":
        out["receipt"] = "browser" if want_receipt else "none"
        out["drawer"] = "unsupported" if want_drawer else "none"
        return out
    pulse_with_receipt = want_drawer and want_receipt and not drawer_first
    try:
        if want_drawer and not pulse_with_receipt:
            out["drawer"] = "sent" if _kick(db, ctx, dest, sale["id"]) else "failed"
        if want_receipt:
            try:
                print_receipt(db, ctx, sale["id"], open_drawer=pulse_with_receipt, target=dest, given=given)
                out["receipt"] = "sent"
                if pulse_with_receipt:
                    out["drawer"] = "sent"
            except PrintFailed as e:
                out["receipt"], out["message"] = "failed", e.message
                if pulse_with_receipt:
                    out["drawer"] = "sent" if (e.details or {}).get("drawer_opened") else "failed"
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
        raise ValidationFailed("اكتب سبب فتح الدرج")
    else:
        label = reason
    from ..sales import cash
    with db.read_snapshot():
        register = registers.require_current(db, ctx)
        shift = cash.require_open_shift(db, ctx)
        if not _drawer_kick_available(db, ctx, register):
            raise Conflict("فتح الدرج متوقف على الجهاز ده", code="drawer_kick_disabled")
        dest = target or _receipt_destination(db, ctx, register)
    error = None
    try:
        if dest == "none":
            raise escpos.PrinterError("الطباعة من المتصفح لا تستطيع فتح الدرج — اختر طابعة إيصال مباشرة من «البرنامج ← طابعة الإيصال والدرج»")
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
    dest = rs.check_target(target, allow_browser=True) if target else _receipt_destination(db, ctx, registers.current(db, ctx))
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
    name = (it["name_ar"] or it["name_en"]) if cfg.get("label_text_lang", "en") == "ar" else (it["name_en"] or it["name_ar"])
    source = cfg["label_barcode_source"]
    value = lb.label_value(dict(it), codes, source)
    return {"name": name or "", "value": value, "code": it["code"] or "",
            "internal_barcode": source == "internal" and value == it["code"],
            "price": rs.label_price(it["public_price"], cfg) if it["public_price"] is not None else "",
            "expiry": f"{exp[5:7]}/{exp[2:4]}" if exp else "", "pharmacy": pharmacy, "phone": phone}


def label_size(cfg: dict, size: str | None = None) -> tuple[float, float]:
    from . import labels as lb, settings as rs
    if cfg["label_lang"] == "a4":                  # one cell of the A4 sheet grid
        return lb.a4_cell(*rs.a4_grid(cfg["label_a4"]))
    try:
        return lb.parse_size(size or cfg["label_size"])
    except escpos.PrinterError as e:
        raise ValidationFailed(str(e)) from e


def label_preview(db: Database, ctx: Context, data: dict | None = None) -> tuple[bytes, list[str], dict]:
    """The label as it will print, for the settings screen (sample item, the saved fields and size).
    Also says which size was drawn and where it came from: the settings, or Windows (driver mode, #100)."""
    import io
    from . import labels as lb, settings as rs
    ctx.require("settings.manage")
    saved = rs.get(db, ctx.org_id)
    cfg = {**saved, **{k: v for k, v in (data or {}).items() if k in ("label_pharmacy", "label_phone", "label_lang", "label_a4")}}
    cfg["label_dpi"] = (data or {}).get("label_dpi") if (data or {}).get("label_dpi") in lb.DPIS else saved["label_dpi"]
    cfg["label_lang"] = cfg["label_lang"] if cfg["label_lang"] in lb.LANGS else saved["label_lang"]
    cfg["label_pharmacy"], cfg["label_phone"] = str(cfg["label_pharmacy"] or "")[:40], str(cfg["label_phone"] or "")[:40]
    if data and "label_barcode_source" in data:
        cfg["label_barcode_source"] = rs.label_barcode_source_ok(data["label_barcode_source"])
    if data and "label_size" in data:
        cfg["label_size"] = rs.label_size_ok(data["label_size"])
    if data and isinstance(data.get("label_fields"), dict):
        cfg["label_fields"] = {**lb.DEFAULT_FIELDS, **{k: bool(v) for k, v in data["label_fields"].items() if k in lb.DEFAULT_FIELDS}}
    if data and "label_rows" in data:
        cfg["label_rows"] = rs.label_rows_ok(data["label_rows"])
    if data and isinstance(data.get("label_gap"), int) and not isinstance(data["label_gap"], bool) \
            and 0 <= data["label_gap"] <= 10:
        cfg["label_gap"] = data["label_gap"]
    if data and data.get("label_text_lang") in ("ar", "en"):
        cfg["label_text_lang"] = data["label_text_lang"]
    if data and "label_pharmacy_en" in data:
        cfg["label_pharmacy_en"] = str(data["label_pharmacy_en"] or "").strip()[:40]
    if data and "label_target" in data:
        try:
            cfg["label_target"] = rs.check_target(data["label_target"], "win:Xprinter XP-235B")
        except ValidationFailed:
            pass                            # half-typed on the screen: the preview keeps the saved printer
    w, h = label_size(cfg)
    source = "settings"
    if cfg["label_lang"] == "driver":
        pg = driver_page(cfg["label_target"])      # #100: the label is drawn on the driver's page
        if pg:
            rows = int(cfg.get("label_rows") or 1)
            h_one = (pg.h_mm - float(cfg.get("label_gap") or 0) * (rows - 1)) / rows
            w, h, cfg["label_dpi"], source = pg.w_mm, h_one, pg.dpi_x, "windows"
    pharmacy, phone = rs.label_identity(db, ctx.org_id, cfg)
    internal = cfg["label_barcode_source"] == "internal"
    sample = {"name": "Sample Item 24 Tab", "value": "8130" if internal else "6221025012340",
              "internal_barcode": internal, "code": "8130", "price": rs.label_price(4000, cfg),
              "expiry": "10/27", "pharmacy": pharmacy, "phone": phone}
    try:
        img, dropped = lb.render_label(sample, w, h, cfg["label_dpi"], cfg["label_fields"])
    except escpos.PrinterError as e:
        raise ValidationFailed(str(e), code="label_too_small") from e
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), dropped, {"size": f"{round(w, 1):g}x{round(h, 1):g}", "source": source}


_PAGE_CACHE: dict[str, tuple[float, object]] = {}
PAGE_CACHE_S = 30.0


def driver_page(target: str):
    """#100: the label page of a Windows printer's driver for the preview, or None (not Windows, no such
    printer, not a label size). Kept 30 s: the preview asks on every change on the screen."""
    from . import driver
    name = win_printer(target)
    if not name:
        return None
    now = time.monotonic()
    hit = _PAGE_CACHE.get(name)
    if hit and now - hit[0] < PAGE_CACHE_S:
        return hit[1]
    try:
        pg = driver.page(name)
        driver.check_label_page(pg)
    except escpos.PrinterError:
        pg = None
    _PAGE_CACHE[name] = (now, pg)
    return pg


def win_printer(target: str | None) -> str:
    """The Windows printer name of a "win:NAME" target, else ""."""
    kind, _, name = str(target or "").partition(":")
    return name.strip() if kind == "win" else ""


# the driver page ran ~1 mm past the right edge of the XP-235B label (photo, 12/11): keep text 1.5 mm in
DRIVER_EDGE_MM = 1.5


def _driver_labels(cfg: dict, wanted: list[tuple[dict, int]], dropped: set | None = None):
    """#100: labels through the printer's Windows driver (like E-Stock): drawn at the driver's page size,
    one page per label. Returns the page used. PrinterError on failure."""
    from . import driver, labels as lb
    name = win_printer(cfg["label_target"])
    if not name:
        raise escpos.PrinterError("الطباعة عن طريق تعريف ويندوز تحتاج إلى طابعة ويندوز (win:اسم الطابعة)")

    rows = int(cfg.get("label_rows") or 1)

    def make(pg):
        if rows == 1:
            out = []
            for data, n in wanted:
                img, gone = lb.render_label(data, pg.w_mm, pg.h_mm, pg.dpi_x, cfg["label_fields"], edge_mm=DRIVER_EDGE_MM)
                out.append((img.rotate(180) if cfg.get("label_flip") else img, n))
                if dropped is not None:
                    dropped.update(gone)
            return out
        # #100: the driver page holds `rows` labels top to bottom with the gap between them (the pharmacy's
        # XP-235B: a 38x25 mm page = two 38x12.5 labels); each label is drawn on its own part of the page
        gap = float(cfg.get("label_gap") or 0)
        row_mm = (pg.h_mm - gap * (rows - 1)) / rows
        if row_mm < 6:
            raise escpos.PrinterError("الصفحة أصغر من هذا العدد من الملصقات: قلّل عدد الملصقات في الصفحة")
        # composed at the labels' own (horizontal) resolution; the driver page stretches it to its pixels,
        # so a driver with different vertical dots per inch keeps the rows where they belong
        dots = lambda mm: round(mm * pg.dpi_x / 25.4)            # noqa: E731
        labels = []
        for data, n in wanted:
            img, gone = lb.render_label(data, pg.w_mm, row_mm, pg.dpi_x, cfg["label_fields"], edge_mm=DRIVER_EDGE_MM)
            labels += [img] * n
            if dropped is not None:
                dropped.update(gone)
        width = max(i.size[0] for i in labels)
        height = dots(pg.h_mm)
        pages = []
        for k in range(0, len(labels), rows):
            page = Image.new("1", (width, height), 1)
            for i, img in enumerate(labels[k:k + rows]):
                page.paste(img, (0, dots(i * (row_mm + gap))))
            pages.append((page.rotate(180) if cfg.get("label_flip") else page, 1))
        return pages
    return driver.print_pages(name, make)


def print_labels(db: Database, ctx: Context, wanted: list[tuple[str, int]], *, size: str | None = None,
                 target: str | None = None) -> dict:
    """Barcode labels straight to the label printer (TSPL for Xprinter/TSC, ZPL for Zebra)."""
    from . import labels as lb
    ctx.require("items.read")
    cfg = label_config(db, ctx)
    if cfg["label_lang"] in ("browser", "a4"):
        raise ValidationFailed("طابعة الملصقات مضبوطة على الطباعة من المتصفح", code="labels_in_browser")
    w, h = label_size(cfg, size)
    total = sum(n for _, n in wanted)
    if not wanted or total > lb.MAX_LABELS or any(not isinstance(n, int) or n < 1 for _, n in wanted):
        raise ValidationFailed(f"عدد الملصقات من 1 إلى {lb.MAX_LABELS}")
    imgs, dropped = [], set()
    try:
        datas = []
        for iid, n in wanted:
            it = db.one("SELECT * FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
            if not it or (it["hidden"] and not ctx.can("items.hidden")):
                continue
            datas.append((label_data(db, ctx, it, cfg), n))
        if not datas:
            raise ValidationFailed("اختر الأصناف")
        if cfg["label_lang"] == "driver":
            pg = _driver_labels({**cfg, "label_target": target or cfg["label_target"]}, datas, dropped)
            w, h = round(pg.w_mm, 1), round(pg.h_mm, 1)
        else:
            for data, n in datas:
                img, gone = lb.render_label(data, w, h, cfg["label_dpi"], cfg["label_fields"])
                imgs.append((img, n))
                dropped.update(gone)
            escpos.send(target or cfg["label_target"] or "none",
                        lb.job(cfg["label_lang"], imgs, w, h, cfg["label_gap"], lb.shift_dots(cfg["label_shift"], cfg["label_dpi"]),
                               _variant(cfg), cfg["label_dpi"]))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك") from e
    with db.transaction():
        audit.record(db, ctx, "print.labels", "item", None,
                     after={"labels": total, "size": f"{w:g}x{h:g}", "lang": cfg["label_lang"]})
    return {"ok": True, "labels": total, "dropped": sorted(dropped), "size": f"{w:g}x{h:g}"}


def _variant(cfg: dict) -> str:
    """The saved TSPL job variant (#100); an unknown stored value falls back to the default job."""
    from . import labels as lb
    v = cfg.get("label_variant")
    return v if v in lb.VARIANTS else "current"


def _label_cfg(db: Database, ctx: Context, data: dict) -> dict:
    """The saved label settings with the screen's unsaved values on top, each one validated (#100)."""
    from . import labels as lb, settings as rs
    cfg = label_config(db, ctx)
    if "label_lang" in data:
        if data["label_lang"] not in lb.LANGS:
            raise ValidationFailed("نوع طابعة الملصقات غير صحيح")
        cfg["label_lang"] = data["label_lang"]
    if cfg["label_lang"] not in lb.DIRECT:
        raise ValidationFailed("اختر نوع طابعة الملصقات (تعريف ويندوز أو TSPL أو ZPL) لتجربة الطباعة المباشرة",
                               code="labels_in_browser")
    if "label_barcode_source" in data:
        cfg["label_barcode_source"] = rs.label_barcode_source_ok(data["label_barcode_source"])
    if "label_size" in data:
        cfg["label_size"] = rs.label_size_ok(data["label_size"])
    if "label_target" in data:
        cfg["label_target"] = rs.check_target(data["label_target"], "win:Xprinter XP-365B")
    if "label_dpi" in data:
        if data["label_dpi"] not in lb.DPIS:
            raise ValidationFailed("دقة الطابعة 203 أو 300")
        cfg["label_dpi"] = data["label_dpi"]
    if "label_gap" in data:
        if not isinstance(data["label_gap"], int) or isinstance(data["label_gap"], bool) or not 0 <= data["label_gap"] <= 10:
            raise ValidationFailed("المسافة بين الملصقات من 0 إلى 10 مم")
        cfg["label_gap"] = data["label_gap"]
    if "label_shift" in data:
        cfg["label_shift"] = rs.label_shift_ok(data["label_shift"])
    if "label_variant" in data:
        cfg["label_variant"] = rs.label_variant_ok(data["label_variant"])
    if "label_flip" in data:
        cfg["label_flip"] = rs.label_flip_ok(data["label_flip"])
    if "label_rows" in data:
        cfg["label_rows"] = rs.label_rows_ok(data["label_rows"])
    if data.get("label_text_lang") in ("ar", "en"):
        cfg["label_text_lang"] = data["label_text_lang"]
    if "label_pharmacy_en" in data:
        cfg["label_pharmacy_en"] = str(data["label_pharmacy_en"] or "").strip()[:40]
    cfg["label_variant"] = _variant(cfg)
    if isinstance(data.get("label_fields"), dict):
        cfg["label_fields"] = {**cfg["label_fields"], **{k: bool(v) for k, v in data["label_fields"].items() if k in lb.DEFAULT_FIELDS}}
    if not cfg["label_target"]:
        raise ValidationFailed("اختر طابعة الملصقات (مكان الطابعة) أولًا", code="no_printer")
    if cfg["label_lang"] == "driver" and not win_printer(cfg["label_target"]):
        raise ValidationFailed("الطباعة عن طريق تعريف ويندوز تحتاج إلى طابعة ويندوز: win:اسم الطابعة", code="driver_needs_windows_printer")
    return cfg


def print_label_test(db: Database, ctx: Context, data: dict | None = None) -> dict:
    """#100: a few sample labels with the values on the settings screen, saved or not (size, gap, shift, dpi,
    type, printer, fields, job variant), so the owner can tune the roll in one go. No item, nothing saved."""
    from . import labels as lb, settings as rs
    ctx.require("settings.manage")
    cfg = _label_cfg(db, ctx, data or {})
    variant = cfg["label_variant"]
    if cfg["label_lang"] == "driver":
        variant = "current"                 # the driver builds the job itself: TSPL variants do not apply
    elif variant != "current" and cfg["label_lang"] != "tspl":
        raise ValidationFailed("طرق الإرسال البديلة لطابعات TSPL فقط", code="variant_tspl_only")
    w, h = label_size(cfg)
    pharmacy, phone = rs.label_identity(db, ctx.org_id, cfg)
    internal = cfg["label_barcode_source"] == "internal"
    sample = {"name": "Sample Item 24 Tab", "value": "8130" if internal else "6221025012340",
              "internal_barcode": internal, "code": "8130", "price": rs.label_price(4000, cfg),
              "expiry": "10/27", "pharmacy": pharmacy, "phone": phone}
    shift = lb.shift_dots(cfg["label_shift"], cfg["label_dpi"])
    count = lb.TEST_LABELS
    try:
        if cfg["label_lang"] == "driver":
            rows = int(cfg.get("label_rows") or 1)
            count = -(-lb.TEST_LABELS // rows) * rows          # whole pages: no blank half at the end (owner, 12/11)
            pg = _driver_labels(cfg, [(sample, count)])
            w, h = round(pg.w_mm, 1), round(pg.h_mm, 1)
        else:
            img, _ = lb.render_label(sample, w, h, cfg["label_dpi"], cfg["label_fields"])
            escpos.send(cfg["label_target"], lb.job(cfg["label_lang"], [(img, lb.TEST_LABELS)], w, h, cfg["label_gap"], shift,
                                                    variant, cfg["label_dpi"]))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e)) from e
    with db.transaction():
        audit.record(db, ctx, "print.label_test", "printer", None,
                     after={"target": cfg["label_target"], "size": f"{w:g}x{h:g}", "gap": cfg["label_gap"], "flip": cfg["label_flip"],
                            "shift_mm": cfg["label_shift"], "lang": cfg["label_lang"], "labels": count,
                            "variant": variant})
    return {"ok": True, "labels": count, "variant": variant, "size": f"{w:g}x{h:g}"}


def calibrate_labels(db: Database, ctx: Context, data: dict | None = None) -> dict:
    """#100: «معايرة بكرة الملصقات»: TSPL GAPDETECT for the roll on the screen (size and gap, saved or not), so
    the printer's gap sensor learns this roll. The printer feeds a few blank labels; nothing is saved."""
    from . import labels as lb
    ctx.require("settings.manage")
    data = data or {}
    cfg = _label_cfg(db, ctx, data)
    if cfg["label_lang"] == "driver":
        raise ValidationFailed("في الطباعة عن طريق تعريف ويندوز، المعايرة من «تفضيلات الطباعة» للطابعة في ويندوز"
                               " (أو من زر الطابعة نفسها)", code="calibrate_in_windows")
    if cfg["label_lang"] != "tspl":
        raise ValidationFailed("المعايرة من البرنامج لطابعات TSPL (Xprinter / TSC) فقط", code="calibrate_tspl_only")
    mode = data.get("mode", "sized")
    if mode not in lb.CALIBRATE_MODES:
        raise ValidationFailed("طريقة المعايرة: sized أو auto")
    w, h = label_size(cfg)
    if mode == "sized" and cfg["label_gap"] < 1:
        raise ValidationFailed("المعايرة تحتاج إلى مسافة بين الملصقات 1 مم على الأقل")
    try:
        escpos.send(cfg["label_target"], lb.calibrate_job(w, h, cfg["label_gap"], cfg["label_dpi"], mode))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e)) from e
    with db.transaction():
        audit.record(db, ctx, "print.label_calibrate", "printer", None,
                     after={"target": cfg["label_target"], "size": f"{w:g}x{h:g}", "gap": cfg["label_gap"],
                            "dpi": cfg["label_dpi"], "mode": mode})
    return {"ok": True, "mode": mode}
