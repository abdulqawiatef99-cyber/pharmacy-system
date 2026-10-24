"""How the cashier receipt looks (owner sets it once)."""
from __future__ import annotations

import json

from ..core import audit
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database

DEFAULTS = {"paper": 80, "title": "", "lines": ["", ""], "footer": "شكرًا لزيارتكم — ألف سلامة",
            "names": "en", "show_cashier": True, "show_customer": True, "show_expiry": False,
            "show_return_policy": True, "label_size": "38x25"}
LABEL_SIZES = ("38x25", "50x30", "58x40")


def get(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='receipt'", (org_id,))
    try:
        stored = json.loads(r["value"]) if r else {}
    except ValueError:
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(db: Database, ctx: Context, data: dict) -> dict:
    ctx.require("settings.manage")
    cur = get(db, ctx.org_id)
    new = dict(cur)
    if "paper" in data:
        if data["paper"] not in (80, 58):
            raise ValidationFailed("مقاس الورق 80 أو 58 مم")
        new["paper"] = data["paper"]
    if "names" in data:
        if data["names"] not in ("en", "ar", "both"):
            raise ValidationFailed("اختيار غير صحيح")
        new["names"] = data["names"]
    if "label_size" in data:
        if data["label_size"] not in LABEL_SIZES:
            raise ValidationFailed("مقاس الملصق غير صحيح")
        new["label_size"] = data["label_size"]
    for k in ("title", "footer"):
        if k in data:
            new[k] = str(data[k] or "").strip()[:80]
    if "lines" in data:
        if not isinstance(data["lines"], list):
            raise ValidationFailed("بيانات غير صحيحة")
        new["lines"] = [str(x or "").strip()[:60] for x in data["lines"][:3]]
    for k in ("show_cashier", "show_customer", "show_expiry", "show_return_policy"):
        if k in data:
            new[k] = bool(data[k])
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'receipt', ?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, json.dumps(new, ensure_ascii=False)))
        audit.record(db, ctx, "settings.receipt", "organization", ctx.org_id, before=cur, after=new)
    return new
