"""The pharmacy logo printed at the top of the receipt (F27).

Uploaded once from Settings → receipt (the browser shrinks it first), cleaned here and stored as a
small grayscale PNG under its own org_settings key, so the receipt settings and their audit trail
never carry the image itself. Thermal printers have no grey: the receipt renderer turns it into
black/white after scaling, so a flat black-and-white logo prints best."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io

from PIL import Image, ImageOps

from ..core.errors import ValidationFailed

KEY = "receipt_logo"
MAX_UPLOAD = 180 * 1024            # decoded bytes (the request body limit is 256 KB of JSON)
MAX_PIXELS = 4000 * 4000           # refuse decompression bombs before decoding
STORE_W, STORE_H = 576, 240        # full 80 mm width; 30 mm high at 203 dpi


def clean(data_url: str) -> bytes:
    """data:image/png;base64,... (or plain base64) -> trimmed grayscale PNG bytes."""
    raw = str(data_url or "")
    if raw.startswith("data:"):
        head, _, raw = raw.partition(",")
        if ";base64" not in head:
            raise ValidationFailed("صيغة صورة اللوجو غير صحيحة")
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationFailed("صيغة صورة اللوجو غير صحيحة")
    if not blob or len(blob) > MAX_UPLOAD:
        raise ValidationFailed("صورة اللوجو كبيرة؛ الحد 180 ك.ب")
    try:
        img = Image.open(io.BytesIO(blob))
        if img.format not in ("PNG", "JPEG", "WEBP", "GIF", "BMP"):
            raise ValidationFailed("يجب أن يكون اللوجو صورة PNG أو JPG")
        if img.width * img.height > MAX_PIXELS:
            raise ValidationFailed("أبعاد صورة اللوجو كبيرة جدًا")
        img.load()
    except ValidationFailed:
        raise
    except Exception:  # noqa: BLE001 — any undecodable upload is the user's file, not a server fault
        raise ValidationFailed("تعذر قراءة صورة اللوجو")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("L")
    box = ImageOps.invert(img).point(lambda p: 255 if p > 55 else 0).getbbox()   # trim the white margin
    if not box:
        raise ValidationFailed("صورة اللوجو فارغة (كلها بيضاء)")
    img = img.crop(box)
    img.thumbnail((STORE_W, STORE_H), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def get_png(db, org_id: str) -> bytes | None:
    r = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
    if not r or not r["value"]:
        return None
    try:
        return base64.b64decode(r["value"])
    except (binascii.Error, ValueError):
        return None


def store(db, org_id: str, png: bytes | None) -> dict:
    """Inside the caller's transaction. Returns what the audit record keeps (never the image)."""
    if png is None:
        db.execute("DELETE FROM org_settings WHERE org_id=? AND key=?", (org_id, KEY))
        return {"logo": None}
    db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, ?, ?) ON CONFLICT(org_id, key)"
               " DO UPDATE SET value=excluded.value", (org_id, KEY, base64.b64encode(png).decode()))
    w, h = Image.open(io.BytesIO(png)).size
    return {"logo": {"sha256": hashlib.sha256(png).hexdigest()[:16], "bytes": len(png), "size": f"{w}x{h}"}}


def bitmap(png: bytes, max_w: int, max_h: int) -> Image.Image:
    """Grayscale logo scaled to fit (never enlarged past 2x, which only blurs it)."""
    img = Image.open(io.BytesIO(png)).convert("L")
    scale = min(max_w / img.width, max_h / img.height, 2.0)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.LANCZOS) if size != img.size else img


def print_look(png: bytes) -> bytes:
    """The logo exactly as the thermal printer will put it on paper (black/white), for the settings preview:
    a coloured logo can lose parts (dark-coloured text on a dark box prints as one black block)."""
    from .escpos import THRESHOLD
    img = Image.open(io.BytesIO(png)).convert("L").point(lambda p: 255 if p > THRESHOLD else 0)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
