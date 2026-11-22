"""U2-21 dispensing notes: a reminder the cashier sees once per invoice when an item with a listed active ingredient
is on it. Informational only: it never blocks the sale and asks nothing.

Enabled by default only for the ingredients the owner confirmed from the EDA decree 375/2026 guide: gabapentin,
eszopiclone, cyclopentolate, tropicamide, clozapine. The rules shown are the guide's, as the owner gave them. Any
other ingredient the owner adds starts disabled and marked «محتاج تأكيد» until he turns it on.

Stored as one JSON value in org_settings (key 'dispensing_notes'): no database change. An item matches when its
active ingredient (or its ingredient key) contains the listed name as a whole word, or its English name does.
"""
from __future__ import annotations

import json
import re

from ..catalog.normalize import normalize_text
from ..core import audit
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database

KEY = "dispensing_notes"
SOURCE = "دليل قرار هيئة الدواء 375 لسنة 2026"
RULES_375 = ["روشتة سارية", "أقصى كمية تكفي شهر", "الروشتة تتصرف لحد 3 مرات أو المدة المكتوبة فيها",
             "يتسجل في دفتر الوارد والمنصرف"]
DEFAULTS = {name: {"enabled": True, "confirmed": True, "rules": RULES_375, "source": SOURCE}
            for name in ("gabapentin", "eszopiclone", "cyclopentolate", "tropicamide", "clozapine")}
MAX_NOTES, MAX_RULES, MAX_TEXT = 100, 8, 160


def get(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    try:
        stored = json.loads(r["value"]) if r else {}
    except ValueError:
        stored = {}
    out = json.loads(json.dumps(DEFAULTS))
    for name, v in (stored if isinstance(stored, dict) else {}).items():
        if isinstance(v, dict):
            out[name] = {**out.get(name, {"enabled": False, "confirmed": False, "rules": [], "source": None}), **v}
    return out


def save(db: Database, ctx: Context, notes: dict) -> dict:
    """Owner / settings.manage. notes: {ingredient: {enabled?, rules?}}; a new ingredient starts disabled and
    unconfirmed; turning one on confirms it."""
    ctx.require("settings.manage")
    if not isinstance(notes, dict) or len(notes) > MAX_NOTES:
        raise ValidationFailed("بيانات غير صحيحة")
    before = get(db, ctx.org_id)
    new = json.loads(json.dumps(before))
    for raw, v in notes.items():
        name = normalize_text(str(raw))[:60]
        if not name or not isinstance(v, dict):
            raise ValidationFailed("اكتب اسم المادة الفعالة")
        cur = new.get(name) or {"enabled": False, "confirmed": False, "rules": [], "source": None}
        if v.get("remove"):
            if name in DEFAULTS:
                raise ValidationFailed("المادة دي من دليل القرار: تتقفل بس، ماتتمسحش")
            new.pop(name, None)
            continue
        if "rules" in v:
            rules = v["rules"]
            if not isinstance(rules, list) or len(rules) > MAX_RULES:
                raise ValidationFailed(f"حتى {MAX_RULES} سطور")
            cur["rules"] = [str(x).strip()[:MAX_TEXT] for x in rules if str(x).strip()]
        if "enabled" in v:
            cur["enabled"] = bool(v["enabled"])
            if cur["enabled"]:
                cur["confirmed"] = True
        new[name] = cur
    stored = {k: v for k, v in new.items() if DEFAULTS.get(k) != v}
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, KEY, json.dumps(stored, ensure_ascii=False)))
        audit.record(db, ctx, "settings.dispensing_notes", "organization", ctx.org_id, before=before, after=new)
    return get(db, ctx.org_id)


def _has(text: str | None, name: str) -> bool:
    return bool(text) and re.search(rf"(?<![a-z؀-ۿ]){re.escape(name)}(?![a-z؀-ۿ])",
                                    normalize_text(text)) is not None


def for_items(db: Database, org_id: str, items: list[dict]) -> list[dict]:
    """The enabled notes that apply to these items (dicts with active_ingredient / ingredient_key / name_en / name),
    one per ingredient, in the invoice's order."""
    notes = {k: v for k, v in get(db, org_id).items() if v.get("enabled") and v.get("rules")}
    out, seen = [], set()
    for it in items:
        for name, v in notes.items():
            if name in seen:
                continue
            if any(_has(it.get(f), name) for f in ("active_ingredient", "ingredient_key", "name_en", "name_ar")):
                seen.add(name)
                out.append({"ingredient": name, "rules": v["rules"], "source": v.get("source"),
                            "item": it.get("name") or it.get("name_en")})
    return out
