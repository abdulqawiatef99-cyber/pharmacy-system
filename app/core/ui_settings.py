"""تخصيص الواجهة لكل صيدلية (المالك أو من معه settings.manage): الشاشات الظاهرة في الشريط العلوي
وترتيبها، أزرار الصفحة الرئيسية، اختصارات Alt+رقم، والأعمدة المخفية في الجداول الكبيرة.
تُحفظ كقيمة JSON واحدة في org_settings (المفتاح 'ui')؛ لا يوجد أي تغيير في بنية قاعدة البيانات."""
from __future__ import annotations

import json

from . import audit
from .errors import ValidationFailed
from .permissions import Context
from ..db import Database

# every screen that can sit in the top bar / on the home page / behind a shortcut
SCREENS = ("pos", "sales", "purchases", "items", "counts", "shift", "customers", "reports", "owner-reports", "expenses",
           "transfers", "profit", "shortages", "suppliers", "supplier-returns", "procurement", "deadstock", "recalls",
           "import", "users", "audit", "staff", "system", "me", "feedback", "groups", "notifications", "customize")
TABLES = {"invoices": 17, "items": 7}            # table -> number of columns that can be hidden (by position)
MAX_MAIN = 8
MAX_HOME = 8
DEFAULTS = {
    "nav_main": ["pos", "sales", "purchases", "items", "counts"],
    "home": ["pos", "purchases", "items", "counts", "shift"],
    "shortcuts": {"1": "pos", "2": "sales", "3": "items", "4": "purchases", "5": "shift", "6": "customers", "7": "reports"},
    "columns": {},
    "pos_keys": {},              # only the keys changed from POS_KEYS_DEFAULT (U2-19)
}
# U2-19: the sale screen's keys, changeable in «تخصيص الواجهة». The arrows, Delete, Esc, F12 (the scanner sends it)
# and F8 (the drawer, printed on the drawer label) stay fixed; F5 (reload) is never offered.
POS_KEYS_DEFAULT = {"search": "F1", "branch_stock": "F2", "item_card": "F3", "receipt": "F4", "qty": "F6",
                    "exchange": "F7", "done": "F9", "pay": "F10", "drawer": "F11", "new_sale": "Insert",
                    "next_tab": "Ctrl+Tab", "cancel_sale": "Ctrl+Delete"}
# never a key used while typing (Home/End/PageUp/PageDown, the number pad without NumLock) or Ctrl+Insert (copy)
POS_KEYS_ALLOWED = ("F1", "F2", "F3", "F4", "F6", "F7", "F9", "F10", "F11", "Insert", "Ctrl+Tab", "Ctrl+Delete",
                    "Ctrl+Enter")


def pos_keys(ui: dict) -> dict:
    """The full action -> key map: the defaults with this pharmacy's changes."""
    return {**POS_KEYS_DEFAULT, **(ui.get("pos_keys") or {})}


def get(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='ui'", (org_id,))
    try:
        stored = json.loads(r["value"]) if r else {}
    except ValueError:
        stored = {}
    out = json.loads(json.dumps(DEFAULTS))
    for k in DEFAULTS:
        if k in stored:
            out[k] = stored[k]
    out["pos_keys"] = pos_keys(out)            # always the full map: the screens read it as it is
    return out


def _screens(v, limit: int, what: str) -> list[str]:
    if not isinstance(v, list) or len(v) > limit or len(set(v)) != len(v) or any(x not in SCREENS for x in v):
        raise ValidationFailed(f"{what}: اختيار غير صحيح (حتى {limit})")
    return list(v)


def save(db: Database, ctx: Context, changes: dict) -> dict:
    ctx.require("settings.manage")
    if not isinstance(changes, dict):
        raise ValidationFailed("بيانات غير صحيحة")
    before = get(db, ctx.org_id)
    new = json.loads(json.dumps(before))
    if changes.get("reset"):
        new = json.loads(json.dumps(DEFAULTS))
    if "nav_main" in changes:
        new["nav_main"] = _screens(changes["nav_main"], MAX_MAIN, "الشريط العلوي")
    if "home" in changes:
        new["home"] = _screens(changes["home"], MAX_HOME, "أزرار الصفحة الرئيسية")
    if "shortcuts" in changes:
        sc = changes["shortcuts"]
        if not isinstance(sc, dict) or any(k not in "123456789" or len(k) != 1 or v not in SCREENS for k, v in sc.items()):
            raise ValidationFailed("الاختصارات: اختيار غير صحيح")
        new["shortcuts"] = {k: sc[k] for k in sorted(sc)}
    if "pos_keys" in changes:
        pk = changes["pos_keys"]
        if pk is None or pk == {}:
            new["pos_keys"] = dict(POS_KEYS_DEFAULT)              # back to the defaults
        else:
            if not isinstance(pk, dict) or any(a not in POS_KEYS_DEFAULT or k not in POS_KEYS_ALLOWED
                                               for a, k in pk.items()):
                raise ValidationFailed("اختصارات شاشة البيع: اختيار غير صحيح")
            full = {**pos_keys(new), **pk}
            seen: dict = {}
            for a, k in full.items():
                if k in seen:
                    raise ValidationFailed(f"الزرار {k} متاخد لحاجة تانية في شاشة البيع", code="key_conflict",
                                           details={"key": k, "actions": [seen[k], a]})
                seen[k] = a
            new["pos_keys"] = full
    if "columns" in changes:
        cols = changes["columns"]
        if not isinstance(cols, dict):
            raise ValidationFailed("الأعمدة: اختيار غير صحيح")
        for t, hidden in cols.items():
            if t not in TABLES or not isinstance(hidden, list) or any(not isinstance(i, int) or not 0 <= i < TABLES[t] for i in hidden):
                raise ValidationFailed("الأعمدة: اختيار غير صحيح")
            if len(set(hidden)) >= TABLES[t]:
                raise ValidationFailed("لا يمكن إخفاء كل الأعمدة")
            new["columns"][t] = sorted(set(hidden))
    stored = {**new, "pos_keys": {a: k for a, k in pos_keys(new).items() if POS_KEYS_DEFAULT[a] != k}}
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'ui', ?)"
                   " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (ctx.org_id, json.dumps(stored, ensure_ascii=False)))
        audit.record(db, ctx, "settings.ui", "organization", ctx.org_id, before=before, after=new)
    return get(db, ctx.org_id)
