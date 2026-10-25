"""Direct printing of barcode labels on label printers common in Egypt.

- Xprinter label models (XP-360B, XP-365B, XP-370B...) and TSC speak TSPL.
- Zebra models (GK420t, ZD220, ZD421...) speak ZPL.

Each label is drawn as a 1-bit image (name, barcode, code and price), the same layout as the
browser labels, so Arabic names and any size work without fonts inside the printer. The image
is sent with TSPL `BITMAP` or ZPL `^GFA`. Transports are the receipt printer's (tcp:/win:).
Not yet tried on a physical label printer: the owner's printers are the test.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from . import barcode as bc
from .escpos import PrinterError, find_font, shaping_available

LANGS = ("browser", "tspl", "zpl")
DPIS = (203, 300)
MAX_LABELS = 500


def dots(mm: float, dpi: int) -> int:
    return int(round(mm * dpi / 25.4))


def label_value(item: dict, codes: list[str]) -> str:
    """Same choice as the browser labels: a 13-digit code, else the first barcode, else the item code."""
    return next((c for c in codes if len(c) == 13), None) or (codes[0] if codes else None) or item.get("code") or item["id"][-12:]


def render(name: str, value: str, price: str, w_mm: int, h_mm: int, dpi: int = 203, font_path: str | None = None) -> Image.Image:
    """One label as a black-and-white image (mode '1', black = 0)."""
    W, H = dots(w_mm, dpi), dots(h_mm, dpi)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    fp = font_path or find_font()
    pad = max(4, W // 40)
    f_name = ImageFont.truetype(fp, max(14, H // 7))
    f_small = ImageFont.truetype(fp, max(12, H // 9))
    rtl = any("؀" <= ch <= "ۿ" for ch in name)
    if rtl and not shaping_available():
        name = ""          # without Arabic shaping the letters would print reversed and apart: leave the name off
    kw = {"direction": "rtl"} if rtl else {}
    text = name
    while text and d.textlength(text, font=f_name, **kw) > W - 2 * pad:
        text = text[:-2] + "…"
    tw = d.textlength(text, font=f_name, **kw)
    d.text(((W - tw) / 2, pad // 2), text, font=f_name, fill=0, **kw)
    # barcode in the middle band
    try:
        bits = bc.ean13_modules(value)
    except ValueError:
        bits = bc.code128_modules(value)
    quiet = 10
    module = (W - 2 * pad) // (len(bits) + 2 * quiet)
    if module < 1:
        raise PrinterError("الكود أطول من عرض الملصق؛ اختر مقاسًا أعرض")
    bar_w = module * len(bits)
    x0 = (W - bar_w) // 2
    top = pad // 2 + int(f_name.size * 1.3)
    bottom = H - pad // 2 - int(f_small.size * 1.3)
    for i, b in enumerate(bits):
        if b == "1":
            d.rectangle([x0 + i * module, top, x0 + (i + 1) * module - 1, bottom], fill=0)
    y = bottom + 2
    d.text((pad, y), value, font=f_small, fill=0)
    if price:
        pw = d.textlength(price, font=f_small)
        d.text((W - pad - pw, y), price, font=f_small, fill=0)
    return img.point(lambda p: 255 if p > 160 else 0, mode="1")


def _rows(img: Image.Image, black_bit: int) -> tuple[int, int, bytes]:
    """Packed rows, MSB first; black dots become `black_bit`."""
    w, h = img.size
    wb = (w + 7) // 8
    px = img.load()
    out = bytearray()
    for yy in range(h):
        for xb in range(wb):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                black = x < w and px[x, yy] == 0
                if black == bool(black_bit):
                    byte |= 0x80 >> bit
            out.append(byte)
    return wb, h, bytes(out)


def tspl_job(img: Image.Image, copies: int, w_mm: int, h_mm: int, gap_mm: int = 2) -> bytes:
    """TSPL: BITMAP data uses 0 for a printed (black) dot."""
    wb, h, data = _rows(img, black_bit=0)
    head = (f"SIZE {w_mm} mm,{h_mm} mm\r\nGAP {gap_mm} mm,0 mm\r\nDIRECTION 1\r\nREFERENCE 0,0\r\nCLS\r\n"
            f"BITMAP 0,0,{wb},{h},0,").encode("ascii")
    return head + data + f"\r\nPRINT 1,{copies}\r\n".encode("ascii")


def zpl_job(img: Image.Image, copies: int) -> bytes:
    """ZPL: ^GFA graphic field in hex, 1 = black."""
    wb, h, data = _rows(img, black_bit=1)
    total = wb * h
    w = img.size[0]
    return (f"^XA^PW{w}^LL{h}^LH0,0^FO0,0^GFA,{total},{total},{wb},{data.hex().upper()}^FS^PQ{copies},0,1,Y^XZ").encode("ascii")


def job(lang: str, labels: list[tuple[Image.Image, int]], w_mm: int, h_mm: int, gap_mm: int = 2) -> bytes:
    if lang == "tspl":
        return b"".join(tspl_job(img, n, w_mm, h_mm, gap_mm) for img, n in labels)
    if lang == "zpl":
        return b"".join(zpl_job(img, n) for img, n in labels)
    raise PrinterError("اختر نوع طابعة الملصقات (TSPL أو ZPL)")
