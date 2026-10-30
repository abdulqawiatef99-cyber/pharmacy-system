"""How the cashier receipt looks (owner sets it once)."""
from __future__ import annotations

import json
import re

from ..core import audit
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database

DEFAULTS = {"paper": 80, "title": "", "lines": ["", ""], "footer": "شكرًا لزيارتكم — ألف سلامة",
            "names": "en", "show_cashier": True, "show_customer": True, "show_expiry": False,
            "show_return_policy": True, "label_size": "38x25",
            "label_lang": "browser", "label_target": "", "label_dpi": 203, "label_gap": 2,
            "receipt_target": "",
            # #81: no receipt unless the cashier asks or the amount rule picks one; the drawer opens on cash
            # independently of the receipt (direct ESC/POS printers only).
            "auto_receipt": False, "auto_receipt_min": 0, "drawer_on_cash": True, "drawer_timing": "after",
            "time_format": "12", "header": "", "tax_no": "", "cr_no": "", "copies": 1}
LABEL_SIZES = ("38x25", "50x30", "58x40")
DRAWER_TIMINGS = ("before", "with", "after")


def fmt_when(dt, time_format: str = "12") -> str:
    """Date and time on a receipt: 12-hour with ص/م (the default) or 24-hour."""
    if time_format == "24":
        return dt.strftime("%Y-%m-%d %H:%M")
    h = dt.hour % 12 or 12
    return f"{dt:%Y-%m-%d} {h}:{dt:%M} {'ص' if dt.hour < 12 else 'م'}"


def get(db: Database, org_id: str) -> dict:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key='receipt'", (org_id,))
    try:
        stored = json.loads(r["value"]) if r else {}
    except ValueError:
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def check_target(value, example: str = "win:POS-80C", allow_browser: bool = False) -> str:
    """A printer typed or picked on the screen: only a network printer or a Windows printer name,
    never a file path (the server would write wherever it was told). "browser" (receipt only) means
    browser printing on purpose, even where PHARM_PRINTER is set."""
    t = str(value or "").strip()
    if allow_browser and t == "browser":
        return t
    if t and not re.fullmatch(r"tcp:[A-Za-z0-9.\-]{1,64}:\d{2,5}|win:[^\x00-\x1f]{1,80}", t):
        raise ValidationFailed(f"عنوان الطابعة غير صحيح؛ مثال: tcp:192.168.1.50:9100 أو {example}")
    return t


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
    if "label_lang" in data:
        from .labels import LANGS
        if data["label_lang"] not in LANGS:
            raise ValidationFailed("نوع طابعة الملصقات غير صحيح")
        new["label_lang"] = data["label_lang"]
    for k, example in (("label_target", "win:Xprinter XP-365B"), ("receipt_target", "win:POS-80C")):
        if k in data:
            new[k] = check_target(data[k], example, allow_browser=k == "receipt_target")
    if "label_dpi" in data:
        if data["label_dpi"] not in (203, 300):
            raise ValidationFailed("دقة الطابعة 203 أو 300")
        new["label_dpi"] = data["label_dpi"]
    if "label_gap" in data:
        if not isinstance(data["label_gap"], int) or not 0 <= data["label_gap"] <= 10:
            raise ValidationFailed("المسافة بين الملصقات من 0 إلى 10 مم")
        new["label_gap"] = data["label_gap"]
    for k in ("title", "footer", "header"):
        if k in data:
            new[k] = str(data[k] or "").strip()[:80]
    for k in ("tax_no", "cr_no"):
        if k in data:
            new[k] = str(data[k] or "").strip()[:30]
    if "auto_receipt_min" in data:
        v = data["auto_receipt_min"]
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100_000_000:
            raise ValidationFailed("الحد الأدنى للإيصال مبلغ صحيح غير سالب (بالقرش)")
        new["auto_receipt_min"] = v
    if "drawer_timing" in data:
        if data["drawer_timing"] not in DRAWER_TIMINGS:
            raise ValidationFailed("توقيت فتح الدرج: قبل أو مع أو بعد الإيصال")
        new["drawer_timing"] = data["drawer_timing"]
    if "time_format" in data:
        if data["time_format"] not in ("12", "24"):
            raise ValidationFailed("نظام الساعة 12 أو 24")
        new["time_format"] = data["time_format"]
    if "copies" in data:
        if data["copies"] not in (1, 2, 3):
            raise ValidationFailed("عدد النسخ من 1 إلى 3")
        new["copies"] = data["copies"]
    if "lines" in data:
        if not isinstance(data["lines"], list):
            raise ValidationFailed("بيانات غير صحيحة")
        new["lines"] = [str(x or "").strip()[:60] for x in data["lines"][:3]]
    for k in ("show_cashier", "show_customer", "show_expiry", "show_return_policy", "auto_receipt", "drawer_on_cash"):
        if k in data:
            new[k] = bool(data[k])
    with db.transaction():
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'receipt', ?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, json.dumps(new, ensure_ascii=False)))
        audit.record(db, ctx, "settings.receipt", "organization", ctx.org_id, before=cur, after=new)
    return new
