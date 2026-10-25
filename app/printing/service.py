"""Server-side printing for the pharmacy node (printer attached to the PC)."""
from __future__ import annotations

import os

from ..core import accounts, audit
from ..core.errors import DomainError, ValidationFailed
from ..core.permissions import Context
from ..db import Database
from ..sales.service import Sales
from . import escpos


class PrintFailed(DomainError):
    status = 502
    code = "print_failed"


def printer_target() -> str:
    return os.environ.get("PHARM_PRINTER", "none").strip()


def print_receipt(db: Database, ctx: Context, sale_id: str, *, open_drawer: bool = False,
                  target: str | None = None) -> dict:
    ctx.require("sales.read")
    sale = Sales(db).get(ctx, sale_id)
    if sale["status"] != "completed":
        raise ValidationFailed("الإيصال متاح للفواتير المكتملة فقط")
    if open_drawer:
        ctx.require("sales.create")
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
    when = accounts.parse_iso(sale["completed_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
    try:
        from . import settings as rs
        cfg = rs.get(db, ctx.org_id)
        img = escpos.render_receipt(escpos.receipt_lines(org, sale, when, cfg),
                                    width=escpos.DOTS_58MM if cfg["paper"] == 58 else escpos.DOTS_80MM)
        escpos.send(target or printer_target(), escpos.receipt_job(img, open_drawer))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك") from e
    with db.transaction():
        audit.record(db, ctx, "print.receipt", "sale", sale_id, after={"drawer": open_drawer})
    return {"ok": True}


def open_drawer(db: Database, ctx: Context, reason: str, target: str | None = None) -> dict:
    """Opening the drawer without a sale is sensitive: permission + reason + audit."""
    ctx.require("cash.manage")
    if len((reason or "").strip()) < 3:
        raise ValidationFailed("اكتب سبب فتح الخزينة")
    try:
        escpos.send(target or printer_target(), escpos.INIT + escpos.DRAWER_KICK)
    except escpos.PrinterError as e:
        raise PrintFailed(str(e)) from e
    with db.transaction():
        audit.record(db, ctx, "cash.drawer_open", "cash_drawer", None, after={"reason": reason.strip()})
    return {"ok": True}


def print_labels(db: Database, ctx: Context, wanted: list[tuple[str, int]], *, size: str | None = None,
                 target: str | None = None) -> dict:
    """Barcode labels straight to the label printer (TSPL for Xprinter/TSC, ZPL for Zebra)."""
    from . import labels as lb, settings as rs
    ctx.require("items.read")
    cfg = rs.get(db, ctx.org_id)
    if cfg["label_lang"] == "browser":
        raise ValidationFailed("طابعة الملصقات مضبوطة على الطباعة من المتصفح", code="labels_in_browser")
    size = size or cfg["label_size"]
    if size not in rs.LABEL_SIZES:
        raise ValidationFailed("مقاس الملصق غير صحيح")
    w, h = (int(x) for x in size.split("x"))
    total = sum(n for _, n in wanted)
    if not wanted or total > lb.MAX_LABELS or any(not isinstance(n, int) or n < 1 for _, n in wanted):
        raise ValidationFailed(f"عدد الملصقات من 1 إلى {lb.MAX_LABELS}")
    imgs = []
    try:
        for iid, n in wanted:
            it = db.one("SELECT * FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
            if not it or (it["hidden"] and not ctx.can("items.hidden")):
                continue
            codes = [r["barcode"] for r in db.all("SELECT barcode FROM item_barcodes WHERE org_id=? AND item_id=?", (ctx.org_id, iid))]
            value = lb.label_value(dict(it), codes)
            price = f"{it['public_price'] / 100:,.2f} ج" if it["public_price"] is not None else ""
            imgs.append((lb.render(it["name_en"] or it["name_ar"] or "", str(value), price, w, h, cfg["label_dpi"]), n))
        if not imgs:
            raise ValidationFailed("اختر الأصناف")
        escpos.send(target or cfg["label_target"] or "none",
                    lb.job(cfg["label_lang"], imgs, w, h, cfg["label_gap"]))
    except escpos.PrinterError as e:
        raise PrintFailed(str(e) + " — يمكنك الطباعة من المتصفح بدلًا من ذلك") from e
    with db.transaction():
        audit.record(db, ctx, "print.labels", "item", None, after={"labels": total, "size": size, "lang": cfg["label_lang"]})
    return {"ok": True, "labels": total}
