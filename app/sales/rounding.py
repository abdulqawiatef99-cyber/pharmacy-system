"""U2-14 invoice-total rounding: off, 0.25, 0.50 or 1.00 EGP, to the NEAREST step (half up), integer piastres only.

The step is one org setting ('invoice_rounding': '0' | '25' | '50' | '100'). The difference is its own line on the
invoice (sales.rounding), positive or negative; the lines and their discounts are never touched, so the drawer,
the reports and the returns stay exact. Existing pharmacies were set to 0.50 by migration 0042 (the owner's
choice); a new pharmacy picks at setup and has rounding off until it does.

UX-27 (owner 26/11, final): at payment the cashier may also round the cash part by hand (a customer short of coins),
within a small limit the owner sets ('manual_rounding_limit', piasters, 0 = off, at most 5.00 EGP; default 1.00).
It is stored as the same sales.rounding, so the drawer, the reports and the returns treat it like the automatic one.
"""
from __future__ import annotations

from ..core import audit
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database

KEY = "invoice_rounding"
STEPS = (0, 25, 50, 100)
MANUAL_KEY = "manual_rounding_limit"
MANUAL_DEFAULT = 100
MANUAL_MAX = 500


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
    # an exact whole number only: 25.9 or "0.9" is refused, never truncated into another policy (Codex P1 on #415)
    if isinstance(value, bool):
        v = -1
    elif isinstance(value, int):
        v = value
    elif isinstance(value, str) and value.strip().isdigit():
        v = int(value.strip())
    else:
        v = -1
    if v not in STEPS:
        raise ValidationFailed("اختار: من غير تقريب، ربع جنيه، نص جنيه، أو جنيه")
    before = step(db, ctx.org_id)
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, KEY, str(v)))
        audit.record(db, ctx, "settings.invoice_rounding", "organization", ctx.org_id, before=before, after=v)
    return v


def manual_limit(db: Database, org_id: str) -> int:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, MANUAL_KEY))
    if not r:
        return MANUAL_DEFAULT
    try:
        v = int(r["value"])
    except (TypeError, ValueError):
        return 0                                  # an unreadable value never allows more than nothing
    return v if 0 <= v <= MANUAL_MAX else 0


def set_manual_limit(db: Database, ctx: Context, value) -> int:
    ctx.require("settings.manage")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MANUAL_MAX:
        raise ValidationFailed("أقصى تقريب يدوي من صفر لـ 5 جنيه (بالقرش)")
    before = manual_limit(db, ctx.org_id)
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, MANUAL_KEY, str(value)))
        audit.record(db, ctx, "settings.manual_rounding_limit", "organization", ctx.org_id, before=before, after=value)
    return value


def check_manual(value, cash_due: int, limit: int) -> int:
    """The cashier's own rounding of the cash part to a whole pound, within the limit either way, never taking the
    cash part to nothing. Refused, never cut to fit."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationFailed("التقريب اليدوي غير صحيح")
    if not limit:
        raise ValidationFailed("التقريب اليدوي مقفول من الإعدادات", code="manual_rounding_off")
    if abs(value) > limit:
        raise ValidationFailed(f"التقريب اليدوي أكبر من المسموح ({limit / 100:.2f} ج)", code="manual_rounding_limit",
                               details={"limit": limit})
    if cash_due <= 0 or cash_due + value <= 0:
        raise ValidationFailed("التقريب اليدوي على جزء نقدي بس", code="manual_rounding_no_cash")
    # only the whole pound just below or just above (review P1: a bigger jump inside the limit would be a hidden
    # discount or an overcharge that skips the discount limit); never 0, never the amount already round
    below, above = cash_due // 100 * 100, -(-cash_due // 100) * 100
    if value == 0 or cash_due + value not in (below, above):
        raise ValidationFailed("التقريب اليدوي لأقرب جنيه تحت أو فوق بس", code="manual_rounding_whole")
    return value
