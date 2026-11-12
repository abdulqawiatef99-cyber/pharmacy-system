"""Direct printing of barcode labels on label printers common in Egypt.

- Xprinter label models (XP-360B, XP-365B, XP-370B...) and TSC speak TSPL.
- Zebra models (GK420t, ZD220, ZD421...) speak ZPL.

Each label is drawn as a 1-bit image (name, barcode, code and price), the same layout as the
browser labels, so Arabic names and any size work without fonts inside the printer. The image
is sent with TSPL `BITMAP` or ZPL `^GFA`. Transports are the receipt printer's (tcp:/win:).
Not yet tried on a physical label printer: the owner's printers are the test.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from . import barcode as bc
from .escpos import PrinterError, find_font, shaping_available

# a4 = browser printing on an A4 label sheet (grid); driver = through the printer's Windows driver, like
# E-Stock (#100, driver.py): the size and the media handling come from Windows, not from these settings
LANGS = ("browser", "a4", "tspl", "zpl", "driver")
DIRECT = ("tspl", "zpl", "driver")             # printed by the server, no browser dialog
DPIS = (203, 300)
MAX_LABELS = 500
SHIFT_MM = 10           # #100: the vertical shift the settings accept, either way (mm)
TEST_LABELS = 3         # #100: a test run is 3 labels, so a blank label between two printed ones shows


def parse_size(size: str) -> tuple[float, float]:
    """'38x25' or a custom 'W x H' in mm (one decimal allowed): width 20–110, height 10–80."""
    import re
    m = re.fullmatch(r"(\d{2,3}(?:\.\d)?)x(\d{2}(?:\.\d)?)", str(size or ""))
    w, h = (float(m.group(1)), float(m.group(2))) if m else (0, 0)
    if not (20 <= w <= 110 and 10 <= h <= 80):
        raise PrinterError("مقاس الملصق: العرض من 20 إلى 110 مم والارتفاع من 10 إلى 80 مم")
    return w, h


def a4_cell(cols: int, rows: int) -> tuple[float, float]:
    """One cell of an A4 label sheet, rounded DOWN to 0.01 mm so cols x rows never exceeds 210 x 297 mm
    (Codex on #90: rounding 297/24 up to 12.4 mm made 24 rows 297.6 mm and drifted down the sheet)."""
    return math.floor(21000 / cols) / 100, math.floor(29700 / rows) / 100


def dots(mm: float, dpi: int) -> int:
    return int(round(mm * dpi / 25.4))


def label_value(item: dict, codes: list[str]) -> str:
    """Same choice as the browser labels: a 13-digit code, else the first barcode, else the item code."""
    return next((c for c in codes if len(c) == 13), None) or (codes[0] if codes else None) or item.get("code") or item["id"][-12:]


# What can go on a label (#84) and the owner's defaults (the fields printed before #84).
FIELDS = ("pharmacy", "phone", "name", "barcode", "digits", "code", "expiry", "price")
DEFAULT_FIELDS = {"pharmacy": False, "phone": False, "name": True, "barcode": True, "digits": True,
                  "code": False, "expiry": False, "price": True}
DROP_ORDER = ("phone", "pharmacy")      # dropped first when the label is too small; the barcode never shrinks
MIN_BAR_MM = 4.0                        # shortest bars still read easily by a hand scanner
MIN_MODULE_MM = 0.25                    # narrowest bar: 2 dots at 203 dpi, 3 at 300
PAD_MM = 0.6                            # label margin: the same in the image and on the browser page (Codex on #90)


def _rtl(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def _fit(d: ImageDraw.ImageDraw, text: str, font, width: int, **kw) -> str:
    while text and d.textlength(text, font=font, **kw) > width:
        text = text[:-2] + "…"
    return text


def layout(fields: dict, data: dict, w_mm: float, h_mm: float, dpi: int = 203) -> tuple[dict, list[str]]:
    """The fields that are on and have a value, after dropping (phone, then pharmacy name) what the size cannot
    hold. Raises PrinterError when even then the barcode would be too short or too narrow to scan."""
    on = {k: bool(fields.get(k, DEFAULT_FIELDS[k])) and bool(data.get("value" if k in ("barcode", "digits") else k))
          for k in FIELDS}
    if on["digits"] and on["code"]:
        on["digits"] = False            # one code at the bottom left: the internal code wins, as in E-Stock
    H = dots(h_mm, dpi)
    line = max(12, min(dots(h_mm, dpi) // 7, dots(3.2, dpi)))
    row = int(line * 1.2)
    pad = max(3, dots(PAD_MM, dpi))
    dropped = []

    def bar_room():
        rows = (on["phone"] or on["pharmacy"]) + on["name"] + (on["code"] or on["digits"] or on["expiry"] or on["price"])
        return H - 2 * pad - rows * row
    for k in DROP_ORDER:
        if not on["barcode"] or bar_room() >= dots(MIN_BAR_MM, dpi):
            break
        if on[k]:
            on[k] = False
            dropped.append(k)
    if on["barcode"] and bar_room() < dots(MIN_BAR_MM, dpi):
        raise PrinterError("مقاس الملصق لا يتسع للحقول المختارة مع باركود مقروء؛ أوقف بعض الحقول أو اختر مقاسًا أكبر")
    if on["barcode"]:
        bar_module(str(data["value"]), w_mm, dpi)     # every path, browser too (Codex on #90): never too narrow
    return on, dropped


def bar_bits(value: str) -> str:
    try:
        return bc.ean13_modules(value)
    except ValueError:
        return bc.code128_modules(value)


def bar_module(value: str, w_mm: float, dpi: int) -> int:
    """Width of one bar in dots across the label (quiet zones of 10 modules); refuses below 0.25 mm."""
    W = dots(w_mm, dpi)
    module = (W - 2 * max(3, dots(PAD_MM, dpi))) // (len(bar_bits(value)) + 20)
    if module < max(2, dots(MIN_MODULE_MM, dpi)):
        raise PrinterError("الكود أطول من عرض الملصق؛ اختر مقاسًا أعرض")
    return module


def render_label(data: dict, w_mm: float, h_mm: float, dpi: int = 203, fields: dict | None = None,
                 font_path: str | None = None) -> tuple[Image.Image, list[str]]:
    """One label as a 1-bit image, E-Stock layout (#84): phone · pharmacy name / barcode / item name /
    code · expiry · price. data: name, value (barcode), price, code, expiry, pharmacy, phone (all text).
    Returns the image and the fields dropped for lack of room."""
    data = dict(data)
    for k in ("name", "pharmacy"):
        if _rtl(data.get(k) or "") and not shaping_available():
            data[k] = ""        # without Arabic shaping the letters would print reversed and apart: leave it off
    on, dropped = layout(fields or DEFAULT_FIELDS, data, w_mm, h_mm, dpi)
    W, H = dots(w_mm, dpi), dots(h_mm, dpi)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    fp = font_path or find_font()
    line = max(12, min(H // 7, dots(3.2, dpi)))
    row = int(line * 1.2)
    pad = max(3, dots(PAD_MM, dpi))
    font = ImageFont.truetype(fp, line)
    small = ImageFont.truetype(fp, max(11, int(line * 0.9)))
    y = pad

    def text(x_align: str, s: str, f, yy: int, width: int, rtl: bool = False):
        # numbers (price "40.00 ج", phone) stay left to right: with raqm the base direction must be said
        kw = {"direction": "rtl"} if rtl and _rtl(s) else {"direction": "ltr"} if _rtl(s) and shaping_available() else {}
        # shrink the type (down to 70%) before cutting the text with "…" (owner, 12/11: names were cut)
        size = f.size
        while d.textlength(s, font=f, **kw) > width and size > max(10, int(f.size * 0.7)):
            size -= 1
            f = ImageFont.truetype(f.path, size)
        s = _fit(d, s, f, width, **kw)
        tw = d.textlength(s, font=f, **kw)
        x = {"left": pad, "right": W - pad - tw, "center": (W - tw) / 2}[x_align]
        d.text((x, yy), s, font=f, fill=0, **kw)
    if on["phone"] or on["pharmacy"]:
        half = (W - 2 * pad) // (2 if on["phone"] and on["pharmacy"] else 1)
        if on["phone"]:
            text("left", data["phone"], small, y, half)
        if on["pharmacy"]:
            text("right", data["pharmacy"], small, y, half, rtl=True)
        y += row
    bottom_y = H - pad - row if (on["code"] or on["digits"] or on["expiry"] or on["price"]) else H - pad
    name_y = bottom_y - row if on["name"] else bottom_y
    if on["barcode"]:
        bits = bar_bits(str(data["value"]))
        module = bar_module(str(data["value"]), w_mm, dpi)
        x0 = (W - module * len(bits)) // 2
        for i, b in enumerate(bits):
            if b == "1":
                d.rectangle([x0 + i * module, y, x0 + (i + 1) * module - 1, name_y - 2], fill=0)
    if on["name"]:
        text("center", data["name"], font, name_y, W - 2 * pad, rtl=True)
    if bottom_y < H - pad:
        third = (W - 2 * pad) // 3
        left = data["code"] if on["code"] else data.get("value") if on["digits"] else ""
        if left:
            text("left", str(left), small, bottom_y, third if on["expiry"] else 2 * third)
        if on["expiry"]:
            text("center", data["expiry"], small, bottom_y, third)
        if on["price"]:
            text("right", data["price"], small, bottom_y, third)
    return img.point(lambda p: 255 if p > 160 else 0, mode="1"), dropped


def render(name: str, value: str, price: str, w_mm: float, h_mm: float, dpi: int = 203, font_path: str | None = None) -> Image.Image:
    """The default fields (name, barcode with its digits, price)."""
    return render_label({"name": name, "value": value, "price": price}, w_mm, h_mm, dpi, font_path=font_path)[0]


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


# #100 job variants for the TSPL path. The XP-235B printed low and skipped labels with every SHIFT value, so the
# coordinator tries these one after another with the test button and saves the one that prints right.
# "current" is the job sent before #100's follow-up, byte for byte, and stays the default.
#   tear      : reset the printer's stored feed setup before printing: SET PEEL OFF, SET TEAR ON, OFFSET 0 mm,
#               and SHIFT always sent (0 included), so a value left in the printer's memory cannot move the print
#   home      : HOME once before the first label: the printer feeds to the next label's top edge by its sensor
#   per_label : one CLS / BITMAP / PRINT 1 per copy instead of one PRINT 1,n
#   clamp     : the image never ends past the label: its height is cut to label - max(shift, 0) - 0.5 mm, so
#               a downward shift cannot spill onto the next label (which would make the printer skip it)
VARIANTS = {
    "current": frozenset(),
    "tear": frozenset({"tear"}),
    "clamp": frozenset({"clamp"}),
    "tear_clamp": frozenset({"tear", "clamp"}),
    "per_label": frozenset({"per_label"}),
    "tear_clamp_per_label": frozenset({"tear", "clamp", "per_label"}),
    "home": frozenset({"home"}),
    "all": frozenset({"tear", "home", "clamp", "per_label"}),
}
CLAMP_MARGIN_MM = 0.5         # within the label's bottom padding (PAD_MM), so no text is cut at shift 0
CALIBRATE_MODES = ("sized", "auto")


def variant_flags(name: str) -> frozenset:
    if name not in VARIANTS:
        raise PrinterError("طريقة إرسال الملصق غير معروفة: " + " / ".join(VARIANTS))
    return VARIANTS[name]


def clamp_rows(h_mm: float, dpi: int, shift: int) -> int:
    """The tallest image (dots) that ends at least CLAMP_MARGIN_MM before the label's bottom edge after SHIFT."""
    return max(1, dots(h_mm, dpi) - max(0, shift) - dots(CLAMP_MARGIN_MM, dpi))


def tspl_job(img: Image.Image, copies: int, w_mm: float, h_mm: float, gap_mm: int = 2, shift: int = 0,
             flags: frozenset = frozenset(), first: bool = True, dpi: int = 203) -> bytes:
    """TSPL: BITMAP data uses 0 for a printed (black) dot. shift (dots, #100): TSPL `SHIFT`, which moves the
    whole print along the paper; positive = lower on the label, negative = higher. Sent only when not 0
    (always with the "tear" flag). flags: see VARIANTS; the printer setup lines (tear, home) go only in the
    first item's job of a print run. No flags = the job sent before the variants, byte for byte."""
    if "clamp" in flags:
        rows = clamp_rows(h_mm, dpi, shift)
        if img.size[1] > rows:
            img = img.crop((0, 0, img.size[0], rows))
    wb, h, data = _rows(img, black_bit=0)
    tear = "tear" in flags
    head = f"SIZE {w_mm:g} mm,{h_mm:g} mm\r\nGAP {gap_mm} mm,0 mm\r\n"
    if tear and first:
        head += "SET PEEL OFF\r\nSET TEAR ON\r\nOFFSET 0 mm\r\n"
    head += (f"SHIFT {shift}\r\n" if shift or tear else "") + "DIRECTION 1\r\nREFERENCE 0,0\r\n"
    if "home" in flags and first:
        head += "HOME\r\n"
    one = f"CLS\r\nBITMAP 0,0,{wb},{h},0,".encode("ascii") + data
    if "per_label" in flags:
        return head.encode("ascii") + b"".join(one + b"\r\nPRINT 1\r\n" for _ in range(copies))
    return head.encode("ascii") + one + f"\r\nPRINT 1,{copies}\r\n".encode("ascii")


def calibrate_job(w_mm: float, h_mm: float, gap_mm: int, dpi: int, mode: str = "sized") -> bytes:
    """#100: TSPL gap sensor calibration for the roll. "sized": SIZE and GAP of the roll, then GAPDETECT with
    the label length and the gap in dots (the printer feeds a few labels and learns the sensor levels for them);
    "auto": GAPDETECT alone, the printer measures label and gap itself. Nothing is printed."""
    if mode not in CALIBRATE_MODES:
        raise PrinterError("طريقة المعايرة غير صحيحة")
    if mode == "auto":
        return b"GAPDETECT\r\n"
    if gap_mm < 1:
        raise PrinterError("المعايرة تحتاج مسافة بين الملصقات 1 مم على الأقل")
    return (f"SIZE {w_mm:g} mm,{h_mm:g} mm\r\nGAP {gap_mm} mm,0 mm\r\n"
            f"GAPDETECT {dots(h_mm, dpi)},{dots(gap_mm, dpi)}\r\n").encode("ascii")


def zpl_job(img: Image.Image, copies: int, shift: int = 0) -> bytes:
    """ZPL: ^GFA graphic field in hex, 1 = black. shift (dots, #100): ZPL `^LT` label top, positive = lower;
    ZPL accepts -120..120 dots. Always sent, 0 included: Zebra keeps ^LT across formats, so leaving it out
    would keep an earlier shift (e.g. from the test button) after the setting goes back to 0 (Codex on #110)."""
    wb, h, data = _rows(img, black_bit=1)
    total = wb * h
    w = img.size[0]
    top = f"^LT{max(-120, min(120, shift))}"
    return (f"^XA^PW{w}^LL{h}{top}^LH0,0^FO0,0^GFA,{total},{total},{wb},{data.hex().upper()}^FS^PQ{copies},0,1,Y^XZ").encode("ascii")


def shift_dots(shift_mm: float, dpi: int) -> int:
    """The settings' vertical shift (mm, positive = lower on the label) in printer dots."""
    return dots(max(-SHIFT_MM, min(SHIFT_MM, float(shift_mm or 0))), dpi)


def job(lang: str, labels: list[tuple[Image.Image, int]], w_mm: float, h_mm: float, gap_mm: int = 2,
        shift: int = 0, variant: str = "current", dpi: int = 203) -> bytes:
    """variant: TSPL only (#100); the ZPL job is the same for every variant."""
    flags = variant_flags(variant)
    if lang == "tspl":
        return b"".join(tspl_job(img, n, w_mm, h_mm, gap_mm, shift, flags, i == 0, dpi)
                        for i, (img, n) in enumerate(labels))
    if lang == "zpl":
        return b"".join(zpl_job(img, n, shift) for img, n in labels)
    raise PrinterError("اختر نوع طابعة الملصقات (TSPL أو ZPL)")
