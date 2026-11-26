"""Purchase entry preferences and sanity thresholds, stored per pharmacy."""
import json

from ..core import audit
from ..core.errors import ValidationFailed

FIELD_ORDER = ("qty", "expiry", "discount", "bonus", "batch", "public_price", "tax_margin")
THRESHOLDS = {
    "qty_multiple": (5, 1, 100, "مضاعف الكمية المعتادة"),
    "qty_min": (20, 1, 1000000, "أقل كمية للمراجعة"),
    "public_delta_pct": (90, 1, 1000, "فرق السعر عن سعر الجمهور %"),
    "last_cost_delta_pct": (80, 1, 1000, "فرق السعر عن آخر تكلفة %"),
}


def get(db, org_id):
    stored = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='purchase_entry'", (org_id,))
    defaults = {"field_order": list(FIELD_ORDER), **{k: v[0] for k, v in THRESHOLDS.items()}}
    return {**defaults, **(json.loads(stored["value"]) if stored else {})}


def save(db, ctx, changes):
    ctx.require("settings.manage")
    if not isinstance(changes, dict) or set(changes) - {"field_order", *THRESHOLDS}:
        raise ValidationFailed("إعدادات إدخال الشراء غير صحيحة")
    if "field_order" in changes:
        order = changes["field_order"]
        if (not isinstance(order, list) or not all(isinstance(field, str) for field in order)
                or sorted(order) != sorted(FIELD_ORDER)):
            raise ValidationFailed("اختار كل حقول الشراء مرة واحدة في ترتيب الإدخال")
    for key, (_, low, high, label) in THRESHOLDS.items():
        if key in changes and (type(changes[key]) is not int or not low <= changes[key] <= high):
            raise ValidationFailed(f"{label} لازم يكون رقم صحيح من {low} إلى {high}")
    with db.transaction():
        before = get(db, ctx.org_id)
        after = {**before, **changes}
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'purchase_entry', ?)"
                   " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value",
                   (ctx.org_id, json.dumps(after)))
        audit.record(db, ctx, "purchase.entry_settings", "organization", ctx.org_id, before=before, after=after)
    return after
