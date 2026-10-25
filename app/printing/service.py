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
