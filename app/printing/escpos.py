"""Thermal receipt printing (80 mm ESC/POS) + cash drawer.

Why an image: most thermal printers have no Arabic code page, and the ones
that do don't shape/join Arabic letters. So the receipt is rendered as a
bitmap (Pillow + libraqm for correct right-to-left shaping; on Windows Pillow's raqm
needs fribidi-0.dll next to python.exe, which the installer ships) and sent with the
standard ESC/POS raster command `GS v 0`, which practically every 80 mm
ESC/POS printer supports. The drawer is opened by the printer's kick pulse.

Printing runs inside the server on the pharmacy PC (the printer is attached
to that PC), so phones/tablets can print too, and the browser never needs
access to USB devices. Transports:
  tcp:IP:PORT        network printers (usually port 9100)       — tested with a fake socket printer
  win:PRINTER NAME   Windows print queue, RAW data (pywin32)     — NOT tested (needs Windows)
  file:PATH          write bytes to a device/file (/dev/usb/lp0) — tested with a file
Chosen in Settings → receipt (falls back to PHARM_PRINTER); PHARM_RECEIPT_FONT points to a TTF with Arabic.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

DOTS_80MM = 576          # 72 mm printable at 203 dpi (typical 80 mm printer)
DOTS_58MM = 384          # 48 mm printable (58 mm printer)
ESC, GS = b"\x1b", b"\x1d"
THRESHOLD = 160          # grey above this prints white, at or below black (thermal heads have no grey)
INIT = ESC + b"@"
CUT = GS + b"V" + b"\x42" + b"\x00"          # feed and partial cut
DRAWER_KICK = ESC + b"p" + b"\x00" + b"\x19" + b"\xfa"   # pin 2, 50 ms on, 500 ms off

FONT_CANDIDATES = [
    os.environ.get("PHARM_RECEIPT_FONT", ""),
    "C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class PrinterError(RuntimeError):
    pass


def find_font() -> str:
    for f in FONT_CANDIDATES:
        if f and Path(f).exists():
            return f
    raise PrinterError("لا يوجد خط عربي للإيصال؛ حدد PHARM_RECEIPT_FONT")


def shaping_available() -> bool:
    return bool(features.check("raqm"))


def render_receipt(lines: list[tuple[str, str, str]], width: int = DOTS_80MM,
                   font_path: str | None = None) -> Image.Image:
    """lines: (kind, right_text, left_text). kind: title | text | row | total | rule | small.
    Right text is the Arabic label (RTL), left text the amount (LTR)."""
    if not shaping_available():
        raise PrinterError("مكتبة تشكيل الحروف العربية (raqm) غير متاحة؛ الإيصال العربي سيظهر مقطعًا")
    fp = font_path or find_font()
    fonts = {"title": ImageFont.truetype(fp, 34), "text": ImageFont.truetype(fp, 24),
             "row": ImageFont.truetype(fp, 24), "total": ImageFont.truetype(fp, 30),
             "small": ImageFont.truetype(fp, 20)}
    pad, y = 8, 6
    height = sum({"title": 48, "total": 44, "rule": 14, "small": 28}.get(k, 34) for k, _, _ in lines) + 40
    img = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(img)
    for kind, right, left in lines:
        if kind == "rule":
            for x in range(pad, width - pad, 10):
                d.line([(x, y + 6), (x + 5, y + 6)], fill=0, width=2)
            y += 14
            continue
        f = fonts[kind]
        if kind == "title":
            w = d.textlength(right, font=f, direction="rtl")
            d.text(((width - w) / 2, y), right, font=f, fill=0, direction="rtl")
            y += 48
            continue
        if right:
            room = width - 2 * pad - (d.textlength(left, font=f) + 16 if left else 0)
            while right and d.textlength(right, font=f, direction="rtl") > room:
                right = right[:-2] + "…"          # long item names never overrun the amount
            w = d.textlength(right, font=f, direction="rtl")
            d.text((width - pad - w, y), right, font=f, fill=0, direction="rtl")
        if left:
            d.text((pad, y), left, font=f, fill=0, direction="ltr")
        y += {"total": 44, "small": 28}.get(kind, 34)
    return img.crop((0, 0, width, min(height, y + 10)))


def qr_block(width: int, text: str, caption: str = "", font_path: str | None = None) -> Image.Image:
    """A QR code with its caption under it (F27), so the customer knows what scanning it does."""
    from . import qr
    m = qr.encode(text)
    target = 168 if width >= DOTS_80MM else 136          # ~21 / 17 mm at 203 dpi
    module = max(2, min(6, target // (len(m) + 8)))
    q = qr.image(m, module=module, quiet=4)
    if not caption:
        return q
    f = ImageFont.truetype(font_path or find_font(), 20 if width >= DOTS_80MM else 18)
    probe = ImageDraw.Draw(q)
    tw = probe.textlength(caption, font=f, direction="rtl" if shaping_available() else None)
    w = max(q.width, int(tw) + 8)
    img = Image.new("L", (w, q.height + 26), 255)
    img.paste(q, ((w - q.width) // 2, 0))
    ImageDraw.Draw(img).text(((w - tw) / 2, q.height - 6), caption, font=f, fill=0,
                             direction="rtl" if shaping_available() else None)
    return img


def receipt_header(width: int, logo_png: bytes | None = None, qr_text: str = "", qr_caption: str = "",
                   font_path: str | None = None) -> Image.Image | None:
    """Logo and/or QR code above the receipt text (F27): logo on the right and QR on the left
    (Arabic reads right to left), or either one centred. Grayscale; raster_bytes makes it 1-bit."""
    from . import logo as logo_mod
    if not logo_png and not qr_text:
        return None
    pad, gap = 8, 16
    q = qr_block(width, qr_text, qr_caption, font_path) if qr_text else None
    lg = None
    if logo_png:
        room = width - 2 * pad - ((q.width + gap) if q else 0)
        lg = logo_mod.bitmap(logo_png, room, 200 if width >= DOTS_80MM else 160)
    h = max(x.height for x in (q, lg) if x) + 8
    img = Image.new("L", (width, h), 255)
    if lg and q:
        img.paste(lg, (width - pad - lg.width, (h - lg.height) // 2))
        img.paste(q, (pad, (h - q.height) // 2))
    else:
        x = lg or q
        img.paste(x, ((width - x.width) // 2, (h - x.height) // 2))
    return img


def receipt_footer_qr(width: int, text: str, caption: str = "", font_path: str | None = None) -> Image.Image | None:
    """The optional second code, centred under the receipt (F27)."""
    if not text:
        return None
    b = qr_block(width, text, caption, font_path)
    img = Image.new("L", (width, b.height + 12), 255)
    img.paste(b, ((width - b.width) // 2, 6))
    return img


def stack(*parts: Image.Image | None) -> Image.Image:
    parts = [p for p in parts if p is not None]
    out = Image.new("L", (max(p.width for p in parts), sum(p.height for p in parts)), 255)
    y = 0
    for p in parts:
        out.paste(p, (0, y))
        y += p.height
    return out


def raster_bytes(img: Image.Image) -> bytes:
    """ESC/POS `GS v 0` raster image (1 bit per dot, MSB first, black = 1)."""
    bw = img.convert("L").point(lambda p: 255 if p > THRESHOLD else 0, mode="1")
    w, h = bw.size
    wb = (w + 7) // 8
    px = bw.load()
    data = bytearray()
    for yy in range(h):
        for xb in range(wb):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < w and px[x, yy] == 0:
                    byte |= 0x80 >> bit
            data.append(byte)
    return GS + b"v0" + b"\x00" + bytes([wb & 0xFF, wb >> 8, h & 0xFF, h >> 8]) + bytes(data)


def receipt_job(img: Image.Image, open_drawer: bool = False, timing: str = "after", copies: int = 1) -> bytes:
    """One print job; the drawer pulse goes before the receipt, between the image and the cut, or after
    the cut (the owner's setting, #81), and only once whatever the number of copies."""
    kick = DRAWER_KICK if open_drawer else b""
    one = raster_bytes(img) + b"\n\n\n" + CUT
    if timing == "before":
        return INIT + kick + one * copies
    if timing == "with":
        return INIT + raster_bytes(img) + kick + b"\n\n\n" + CUT + one * (copies - 1)
    return INIT + one * copies + kick


# ---------------------------------------------------------------- transports
def send(target: str, data: bytes, timeout: float = 5.0) -> None:
    if not target or target == "none":
        raise PrinterError("لم يتم اختيار طابعة الإيصال (الإعدادات ← الإيصال)")
    kind, _, rest = target.partition(":")
    try:
        if kind == "tcp":
            host, _, port = rest.rpartition(":")
            with socket.create_connection((host, int(port)), timeout=timeout) as s:
                s.sendall(data)
        elif kind == "file":
            with open(rest, "wb") as f:
                f.write(data)
        elif kind == "win":
            _send_windows(rest, data)
        else:
            raise PrinterError(f"نوع طابعة غير معروف: {kind}")
    except PrinterError:
        raise
    except (OSError, ValueError) as e:
        raise PrinterError(f"تعذر الوصول للطابعة: {e}") from e


def windows_printers() -> list[str]:   # pragma: no cover - needs Windows
    """Printer names as Windows lists them (local and shared); empty off Windows."""
    try:
        import win32print  # type: ignore
    except ImportError:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return sorted({p[2] for p in win32print.EnumPrinters(flags, None, 1)})


def _send_windows(printer_name: str, data: bytes) -> None:   # pragma: no cover - needs Windows
    try:
        import win32print  # type: ignore
    except ImportError as e:
        raise PrinterError("الطباعة على Windows تحتاج تثبيت pywin32 (pip install pywin32)") from e
    h = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(h, 1, ("pharmacy-receipt", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def receipt_lines(org_name: str, sale: dict, when: str, cfg: dict | None = None, *, cashier: str = "",
                  given: int = 0) -> list[tuple[str, str, str]]:
    """given: what the customer handed over in cash (piastres), printed with the change due when it is
    more than the cash part of the sale (F27). Shown only on the receipt printed at checkout; it is
    not stored, so a later reprint has no change line."""
    cfg = cfg or {}
    m = lambda p: f"{p / 100:.2f}"  # noqa: E731
    out = [("small", cfg["header"], "")] if cfg.get("header") else []
    out.append(("title", cfg.get("title") or org_name, ""))
    for ln in cfg.get("lines") or []:
        if ln:
            out.append(("small", ln, ""))
    for key, label in (("tax_no", "رقم التسجيل الضريبي"), ("cr_no", "السجل التجاري")):
        if cfg.get(key):
            out.append(("small", f"{label}: {cfg[key]}", ""))
    out.append(("small", f"فاتورة رقم {sale['sale_no']}", when))
    if cashier and cfg.get("show_cashier", True):
        out.append(("small", f"البائع: {cashier}", ""))
    out.append(("rule", "", ""))
    for l in sale["lines"]:
        it = l["item"]
        name = "—" if it.get("hide_on_receipt") else (it.get("name_ar") if cfg.get("names") == "ar" else it.get("name_en")) or it["name"]
        out.append(("row", name, m(l["line_total"])))
        out.append(("small", f"{l['qty_base']} {it['base_unit']}", ""))
    out.append(("rule", "", ""))
    if cfg.get("show_item_count") and sale["lines"]:
        out.append(("small", f"عدد الأصناف: {len(sale['lines'])}", ""))
    if sale["discount_total"] and sale.get("subtotal"):
        out.append(("row", "الإجمالي", m(sale["subtotal"])))
    if sale["discount_total"]:
        out.append(("row", "خصم", m(sale["discount_total"])))
    out.append(("total", "المطلوب", m(sale["total"])))
    for key, label in (("delivery_fee", "رسوم التوصيل"), ("paid_cash", "نقدي"), ("paid_card", "بطاقة"),
                       ("paid_transfer", "تحويل"), ("on_credit", "آجل")):
        if sale.get(key):
            out.append(("row", label, m(sale[key])))
    cash = sale.get("paid_cash") or 0
    if cfg.get("show_change", True) and cash and given > cash:
        out += [("row", "المدفوع من العميل", m(given)), ("total", "الباقي", m(given - cash))]
    out.append(("small", cfg.get("footer") or "شكرًا لزيارتكم", ""))
    return out
