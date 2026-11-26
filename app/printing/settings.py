"""How the cashier receipt looks (owner sets it once)."""
from __future__ import annotations

import json
import re

from ..core import audit
from ..core.errors import ValidationFailed
from ..core.permissions import Context
from ..db import Database
from . import logo, qr, qrlinks

DEFAULTS = {"paper": 80, "template": "detailed", "title": "", "lines": ["", ""], "footer": "شكرًا لزيارتكم — ألف سلامة",
            "names": "en", "show_cashier": True, "show_customer": True, "show_expiry": False,
            "show_return_policy": True, "label_size": "38x25",
            "label_lang": "browser", "label_barcode_source": "internal", "label_target": "", "label_dpi": 203, "label_gap": 2,
            "label_shift": 0,         # #100: move the label print up (negative) or down (positive), mm
            "label_variant": "current",   # #100: how the TSPL job is built (labels.VARIANTS); saved after a device test
            "label_flip": False,          # #100: driver labels turned 180° (a driver set to print upside down)
            "label_rows": 1,              # #100: labels on one driver page, top to bottom (the pharmacy's 38x25 page = 2)
            "receipt_target": "",
            # #81: no receipt unless the cashier asks or the amount rule picks one; the drawer opens on cash
            # independently of the receipt (direct ESC/POS printers only).
            "auto_receipt": False, "auto_receipt_min": 0, "drawer_on_cash": True, "drawer_timing": "after",
            "time_format": "12", "header": "", "tax_no": "", "cr_no": "", "copies": 1,
            # F27: QR code text (a link / WhatsApp), item count and the change due on the receipt.
            # The logo itself lives under its own key (logo.py); get() reports has_logo.
            # qr2 = an optional second code at the bottom (e.g. the location); captions blank = from the link
            "qr_text": "", "qr_caption": "", "qr2_text": "", "qr2_caption": "", "show_item_count": True, "show_change": True,
            # #84: which fields go on a label, and the label's own pharmacy name / phone (blank = the receipt's)
            "label_fields": {}, "label_pharmacy": "", "label_phone": "", "label_a4": "6x24",
            # #100 (owner, 12/11): the label's words in English (default) or Arabic; the English pharmacy name
            "label_text_lang": "en", "label_pharmacy_en": ""}
_KEEP = object()
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
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    from .escpos import RECEIPT_TEMPLATES
    if out["template"] not in RECEIPT_TEMPLATES:
        out["template"] = DEFAULTS["template"]
    from .labels import DEFAULT_FIELDS
    out["label_fields"] = {**DEFAULT_FIELDS, **{k: bool(v) for k, v in (out["label_fields"] or {}).items() if k in DEFAULT_FIELDS}}
    out["has_logo"] = bool(db.one("SELECT 1 FROM org_settings WHERE org_id=? AND key=?", (org_id, logo.KEY)))
    return out


def label_barcode_source_ok(value) -> str:
    from .labels import BARCODE_SOURCES
    if value not in BARCODE_SOURCES:
        raise ValidationFailed("اختر كود الصنف أو الكود الدولي للملصقات")
    return value


def label_size_ok(size) -> str:
    from .escpos import PrinterError
    from .labels import parse_size
    try:
        w, h = parse_size(size)
    except PrinterError as e:
        raise ValidationFailed(str(e)) from e
    return f"{w:g}x{h:g}"


def label_shift_ok(value) -> float:
    """#100: the vertical shift of the label print in mm (positive = lower), one decimal, within ±10 mm."""
    from .labels import SHIFT_MM
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) > SHIFT_MM:
        raise ValidationFailed(f"تحريك الطباعة من -{SHIFT_MM} إلى {SHIFT_MM} مم (سالب = لأعلى، موجب = لأسفل)")
    return round(float(value), 1) + 0.0


def label_variant_ok(value) -> str:
    """#100: one of the named TSPL job variants (labels.VARIANTS)."""
    from .labels import VARIANTS
    if not isinstance(value, str) or value not in VARIANTS:
        raise ValidationFailed("طريقة إرسال الملصق غير معروفة: " + " / ".join(VARIANTS))
    return value


def a4_grid(value) -> tuple[int, int]:
    """'6x24' = 6 columns x 24 rows of labels on one A4 sheet."""
    m = re.fullmatch(r"(\d{1,2})x(\d{1,2})", str(value or ""))
    c, r = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    if not (1 <= c <= 8 and 1 <= r <= 40):
        raise ValidationFailed("شبكة A4: من 1 إلى 8 أعمدة ومن 1 إلى 40 صفًا (مثال 6x24)")
    return c, r


def label_identity(db: Database, org_id: str, cfg: dict) -> tuple[str, str]:
    """Pharmacy name and phone on labels: the label's own values, else the receipt's title and phone line
    (line 2), else the registered name. Nothing invented."""
    name = cfg["label_pharmacy"] or cfg["title"]
    if cfg.get("label_text_lang", "en") == "en" and cfg.get("label_pharmacy_en"):
        name = cfg["label_pharmacy_en"]
    if not name:
        r = db.one("SELECT name FROM organizations WHERE id=?", (org_id,))
        name = r["name"] if r else ""
    lines = cfg["lines"] or []
    return name, cfg["label_phone"] or (lines[1] if len(lines) > 1 else "")


def label_price(piasters: int, cfg: dict) -> str:
    """The price on a label in the label's language: "40.00 EGP" or "40.00 ج"."""
    return f"{piasters / 100:,.2f} " + ("ج" if cfg.get("label_text_lang", "en") == "ar" else "EGP")


def label_rows_ok(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4:
        raise ValidationFailed("عدد الملصقات في الصفحة من 1 إلى 4")
    return value


def label_flip_ok(value) -> bool:
    if not isinstance(value, bool):
        raise ValidationFailed("قلب الملصق: نعم أو لا")
    return value


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
    cur.pop("has_logo")
    new = dict(cur)
    if "paper" in data:
        if data["paper"] not in (80, 58):
            raise ValidationFailed("مقاس الورق 80 أو 58 مم")
        new["paper"] = data["paper"]
    if "template" in data:
        from .escpos import RECEIPT_TEMPLATES
        if data["template"] not in RECEIPT_TEMPLATES:
            raise ValidationFailed("قالب الإيصال غير صحيح")
        new["template"] = data["template"]
    if "names" in data:
        if data["names"] not in ("en", "ar", "both"):
            raise ValidationFailed("اختيار غير صحيح")
        new["names"] = data["names"]
    if "label_barcode_source" in data:
        new["label_barcode_source"] = label_barcode_source_ok(data["label_barcode_source"])
    if "label_size" in data:
        new["label_size"] = label_size_ok(data["label_size"])       # a preset or the owner's own W x H (#84)
    if "label_fields" in data:
        from .labels import DEFAULT_FIELDS
        if not isinstance(data["label_fields"], dict) or set(data["label_fields"]) - set(DEFAULT_FIELDS):
            raise ValidationFailed("حقول الملصق غير صحيحة")
        new["label_fields"] = {**cur["label_fields"], **{k: bool(v) for k, v in data["label_fields"].items()}}
    for k in ("label_pharmacy", "label_phone", "label_pharmacy_en"):
        if k in data:
            new[k] = str(data[k] or "").strip()[:40]
    if "label_text_lang" in data:
        if data["label_text_lang"] not in ("ar", "en"):
            raise ValidationFailed("لغة الملصق: عربي أو إنجليزي")
        new["label_text_lang"] = data["label_text_lang"]
    if "label_a4" in data:
        c, r = a4_grid(data["label_a4"])
        new["label_a4"] = f"{c}x{r}"
    if "label_lang" in data:
        from .labels import LANGS
        if data["label_lang"] not in LANGS:
            raise ValidationFailed("نوع طابعة الملصقات غير صحيح")
        new["label_lang"] = data["label_lang"]
    for k, example in (("label_target", "win:Xprinter XP-365B"), ("receipt_target", "win:POS-80C")):
        if k in data:
            new[k] = check_target(data[k], example, allow_browser=k == "receipt_target")
    if new["label_lang"] == "driver" and new["label_target"] and not new["label_target"].startswith("win:"):
        # #100: the Windows driver prints only to a Windows printer (a network address has no driver here)
        raise ValidationFailed("الطباعة عن طريق تعريف ويندوز تحتاج إلى طابعة ويندوز: win:اسم الطابعة")
    if "label_dpi" in data:
        if data["label_dpi"] not in (203, 300):
            raise ValidationFailed("دقة الطابعة 203 أو 300")
        new["label_dpi"] = data["label_dpi"]
    if "label_gap" in data:
        if not isinstance(data["label_gap"], int) or not 0 <= data["label_gap"] <= 10:
            raise ValidationFailed("المسافة بين الملصقات من 0 إلى 10 مم")
        new["label_gap"] = data["label_gap"]
    if "label_shift" in data:
        new["label_shift"] = label_shift_ok(data["label_shift"])
    if "label_variant" in data:
        new["label_variant"] = label_variant_ok(data["label_variant"])
    if "label_flip" in data:
        new["label_flip"] = label_flip_ok(data["label_flip"])
    if "label_rows" in data:
        new["label_rows"] = label_rows_ok(data["label_rows"])
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
    for k in ("qr_text", "qr2_text"):
        if k in data:
            t = qrlinks.normalize(data[k])          # a mobile number becomes its WhatsApp link
            if t:
                try:
                    qr.encode(t)
                except qr.QRTooLong as e:
                    raise ValidationFailed(str(e))
            new[k] = t
    for k in ("qr_caption", "qr2_caption"):
        if k in data:
            new[k] = str(data[k] or "").strip()[:40]
    png = _KEEP
    if "logo" in data:              # data URL from the settings page; "" / null removes the logo
        png = logo.clean(data["logo"]) if data["logo"] else None
    for k in ("show_cashier", "show_customer", "show_expiry", "show_return_policy", "auto_receipt", "drawer_on_cash",
              "show_item_count", "show_change"):
        if k in data:
            new[k] = bool(data[k])
    if data.get("preview") is True:
        from .service import receipt_preview
        preview_logo = logo.get_png(db, ctx.org_id) if png is _KEEP else png
        return {"png": receipt_preview(db, ctx, new, preview_logo)}
    with db.transaction():
        if png is not _KEEP:
            audit.record(db, ctx, "settings.receipt_logo", "organization", ctx.org_id,
                         after=logo.store(db, ctx.org_id, png))
        db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'receipt', ?) ON CONFLICT(org_id, key)"
                   " DO UPDATE SET value=excluded.value", (ctx.org_id, json.dumps(new, ensure_ascii=False)))
        if set(data) != {"logo"}:          # a logo-only upload leaves the settings record alone
            audit.record(db, ctx, "settings.receipt", "organization", ctx.org_id, before=cur, after=new)
    return get(db, ctx.org_id)
