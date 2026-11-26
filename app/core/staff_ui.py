"""U3-7 small owner requests kept as one org setting (no database change):
- a dhikr ticker at the bottom of the screen («ذِكر»): the owner turns it on or off for everyone and can turn it off
  for some employees; it never covers or blocks a button (the screen draws it as a passive strip);
- per-employee work hours, so the program reminds the employee before his shift ends (a reminder only: it never
  signs anyone out and never stops a sale).
"""
from __future__ import annotations

import json
import re

from . import audit
from .errors import ValidationFailed
from .permissions import Context
from ..db import Database

KEY = "staff_ui"
DHIKR = ["سبحان الله", "الحمد لله", "لا إله إلا الله", "الله أكبر", "سبحان الله وبحمده سبحان الله العظيم",
         "لا حول ولا قوة إلا بالله", "أستغفر الله العظيم", "اللهم صلِّ وسلم على نبينا محمد"]
WARN_MIN = 15
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def get(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    try:
        v = json.loads(r["value"]) if r else {}
    except ValueError:
        v = {}
    return {"dhikr_on": bool(v.get("dhikr_on", False)), "dhikr_off_users": list(v.get("dhikr_off_users") or []),
            "hours": dict(v.get("hours") or {})}


def mine(db: Database, ctx: Context) -> dict:
    """What the screen of the signed-in person shows."""
    v = get(db, ctx.org_id)
    return {"dhikr": DHIKR if v["dhikr_on"] and ctx.user_id not in v["dhikr_off_users"] else [],
            "hours": v["hours"].get(ctx.user_id), "warn_min": WARN_MIN}


def save(db: Database, ctx: Context, data: dict) -> dict:
    ctx.require("users.manage")
    if not isinstance(data, dict):
        raise ValidationFailed("بيانات غير صحيحة")
    before = get(db, ctx.org_id)
    new = json.loads(json.dumps(before))
    users = {r["user_id"] for r in db.all("SELECT user_id FROM memberships WHERE org_id=?", (ctx.org_id,))}
    if "dhikr_on" in data:
        new["dhikr_on"] = bool(data["dhikr_on"])
    if "dhikr_off_users" in data:
        off = data["dhikr_off_users"]
        if not isinstance(off, list) or any(u not in users for u in off):
            raise ValidationFailed("موظف غير معروف")
        new["dhikr_off_users"] = sorted(set(off))
    if "hours" in data:
        h = data["hours"]
        if not isinstance(h, dict):
            raise ValidationFailed("مواعيد غير صحيحة")
        for uid, v in h.items():
            if uid not in users:
                raise ValidationFailed("موظف غير معروف")
            if v is None:
                new["hours"].pop(uid, None)
                continue
            if not isinstance(v, dict) or not _HHMM.match(str(v.get("start", ""))) or not _HHMM.match(str(v.get("end", ""))):
                raise ValidationFailed("اكتب الميعاد بالشكل 10:00")
            new["hours"][uid] = {"start": v["start"], "end": v["end"]}
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, KEY, json.dumps(new)))
        audit.record(db, ctx, "settings.staff_ui", "organization", ctx.org_id, before=before, after=new)
    return get(db, ctx.org_id)
