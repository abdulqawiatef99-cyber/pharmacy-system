"""U2-14 invoice-total rounding: off, 0.25, 0.50 or 1.00 EGP, to the NEAREST step (half up), integer piastres only.

The step is one org setting ('invoice_rounding': '0' | '25' | '50' | '100'). The difference is its own line on the
invoice (sales.rounding), positive or negative; the lines and their discounts are never touched, so the drawer,
the reports and the returns stay exact. Existing pharmacies were set to 0.50 by migration 0042 (the owner's
choice); a new pharmacy picks at setup and has rounding off until it does.
"""
from __future__ import annotations

from ..core import audit
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database

KEY = "invoice_rounding"
STEPS = (0, 25, 50, 100)


def step(db: Database, org_id: str) -> int:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    try:
        v = int(r["value"]) if r else 0
    except (TypeError, ValueError):
        v = 0
    return v if v in STEPS else 0


def nearest(total: int, st: int) -> int:
    """The rounding to add to `total` so it lands on the nearest multiple of `st` (ties go up). 0 when off or
    when the total is not positive."""
    if not st or total <= 0:
        return 0
    r = total % st
    return (st - r) if 2 * r >= st else -r


def set_step(db: Database, ctx: Context, value) -> int:
    ctx.require("settings.manage")
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = -1
    if v not in STEPS:
        raise ValidationFailed("اختار: من غير تقريب، ربع جنيه، نص جنيه، أو جنيه")
    before = step(db, ctx.org_id)
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, KEY, str(v)))
        audit.record(db, ctx, "settings.invoice_rounding", "organization", ctx.org_id, before=before, after=v)
    return v
