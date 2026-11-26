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
  win:PRINTER NAME   Windows print queue, RAW data (pywin32)     — tested with a fake spooler; checked on the
                     pharmacy PC. After sending, the job must leave the queue (or be printing) within a few
                     seconds, else it is cancelled and reported (#98): a queued job would print, and kick
                     the drawer, by itself when the printer is switched on later.
  file:PATH          write bytes to a device/file (/dev/usb/lp0) — tested with a file
Chosen in Settings → receipt (falls back to PHARM_PRINTER); PHARM_RECEIPT_FONT points to a TTF with Arabic.
"""
from __future__ import annotations

import os
import re
import socket
import time
import unicodedata
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


def upper_en(value: str | None) -> str:
    """UX-26 (owner 26/11): English letters in capitals on paper; Arabic and signs such as µ are left alone."""
    return re.sub(r"[a-z]+", lambda m: m.group(0).upper(), value or "")


def text_direction(value: str) -> str:
    """UX-26: the base direction of a name, from its first letter (English -> ltr, Arabic or none -> rtl)."""
    for ch in value or "":
        kind = unicodedata.bidirectional(ch)
        if kind == "L":
            return "ltr"
        if kind in ("R", "AL"):
            return "rtl"
    return "rtl"


ReceiptRow = tuple[str, str, str | tuple[str, ...]]
RECEIPT_TEMPLATES = ("detailed", "compact", "large")


def render_receipt(lines: list[ReceiptRow], width: int = DOTS_80MM, font_path: str | None = None,
                   *, template: str = "detailed", header: Image.Image | None = None,
                   footer_codes: tuple[Image.Image, ...] = ()) -> Image.Image:
    """Lay out receipt rows in printer dots. Pillow/raqm shapes and reorders each cell."""
    from functools import cache

    from .barcode import code128_modules
    from .labels import bold_font

    if not shaping_available():
        raise PrinterError("مكتبة تشكيل الحروف العربية (raqm) غير متاحة؛ الإيصال العربي سيظهر مقطعًا")
    fp = font_path or find_font()
    bold_path = bold_font(fp)
    size = 24 if width >= DOTS_80MM else 20
    size += {"compact": -3, "large": 4}.get(template, 0)
    pad, gap = 8, (5 if template == "compact" else 8)
    span = width - 2 * pad
    y = (header.height if header else 0) + 6
    # Each drawing command uses absolute bounds. The image is allocated after wrapping.
    texts, rules, images = [], [], [(0, 0, header)] if header else []

    @cache
    def font(n, bold):
        return ImageFont.truetype(bold_path if bold else fp, n)

    @cache
    def bounds(value, n, bold, direction):
        return font(n, bold).getbbox(value, anchor="lt", direction=direction)

    def fits(value, room, n, bold, direction):
        x0, _, x1, _ = bounds(value, n, bold, direction)
        return x1 - x0 <= room

    def wrap(value, room, n, bold, direction):
        """Use word breaks first, then split a word that is wider than the cell."""
        for paragraph in value.splitlines():
            if paragraph and fits(paragraph, room, n, bold, direction):
                yield paragraph
                continue
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}" if line else word
                if fits(candidate, room, n, bold, direction):
                    line = candidate
                    continue
                if line:
                    yield line
                while not fits(word, room, n, bold, direction):
                    lo, hi = 1, len(word)
                    while lo + 1 < hi:
                        mid = (lo + hi) // 2
                        if fits(word[:mid], room, n, bold, direction):
                            lo = mid
                        else:
                            hi = mid
                    yield word[:lo]
                    word = word[lo:]
                line = word
            if line:
                yield line

    def cell(value, left, right, n, *, bold=False, direction="rtl", align="right", single=False):
        if not value:
            return 0
        if single:
            while n > 8 and not fits(value, right - left, n, bold, direction):
                n -= 1
        row_y = y
        for part in wrap(value, right - left, n, bold, direction):
            x0, y0, x1, y1 = bounds(part, n, bold, direction)
            start = right - x1 if align == "right" else (left + right - x0 - x1) // 2
            texts.append((start, row_y - y0, part, font(n, bold), direction))
            row_y += max(n + 3, y1 - y0 + 3)
        return row_y - y

    # Cell edges in RTL order. Numeric cells share right edges with their headings.
    edges = [width - pad - span * k // 100 for k in (0, 7, 43, 63, 81, 100)]
    for kind, right, left in lines:
        if kind == "rule":
            rules.append((pad, y + 2, width - pad - 1, y + 2))
            y += gap
        elif kind in ("columns", "item"):
            values = (left[0], right, *left[1:])
            heading = kind == "columns"
            n = size - 2
            h = max(cell(value, edges[i + 1] + 3, edges[i] - 3, n,
                         bold=heading, direction="ltr" if not heading and i in (0, 3, 4)
                         else text_direction(value) if not heading and i == 1 else "rtl",
                         single=heading or i in (0, 3, 4))
                    for i, value in enumerate(values))
            y += h + gap
            if heading:
                rules.append((pad, y - 3, width - pad - 1, y - 3))
        elif kind == "barcode":
            try:
                bits = code128_modules(right)
            except ValueError:
                continue                     # old invoice identifiers may not be printable ASCII
            module = min(4, span // (len(bits) + 20))
            if not module:
                continue
            start = (width - len(bits) * module) // 2
            for i, bit in enumerate(bits):
                if bit == "1":
                    x = start + i * module
                    rules.append((x, y + 4, x + module - 1, y + 49))
            y += 55
            y += cell(right, pad, width - pad, size - 3, direction="ltr", align="center") + gap
        elif kind == "footer_codes":
            for code in footer_codes:
                images.append(((width - code.width) // 2, y, code))
                y += code.height + gap
        else:
            if kind == "detail" and right.startswith("خصم "):
                right = f"خصم \u2066{right.removeprefix('خصم ')}\u2069"
            n = {"title": size + 12, "total": size + 10, "small": size - 2, "customer": size + 2,
                 "center": size - 2, "detail": size - 3}.get(kind, size)
            bold = kind in ("title", "total", "customer")     # UX-26: the customer's details bold and larger
            center = kind in ("title", "center")
            if left:
                # The date and invoice number occupy the same row, even at 58 mm.
                middle = pad + (span // 2 if kind == "small" else span * 42 // 100)
                h1 = cell(right, middle + 4, width - pad, n, bold=bold, single=kind == "small")
                h2 = cell(left, pad, middle - 4, n, bold=bold,
                          direction="ltr", single=True)
                y += max(h1, h2) + gap
            else:
                end = edges[1] - 3 if kind == "detail" else width - pad
                y += cell(right, pad, end, n, bold=bold, align="center" if center else "right") + gap

    img = Image.new("L", (width, y + 6), 255)
    draw = ImageDraw.Draw(img)
    for x, top, part in images:
        img.paste(part, (x, top))
    for box in rules:
        draw.rectangle(box, fill=0)
    for x, top, value, face, direction in texts:
        draw.text((x, top), value, font=face, direction=direction, anchor="lt", fill=0)
    return img


def qr_block(width: int, text: str, caption: str = "", font_path: str | None = None,
             max_w: int | None = None) -> Image.Image:
    """A QR code with its caption under it (F27), so the customer knows what scanning it does."""
    from . import qr
    m = qr.encode(text)
    target = 168 if width >= DOTS_80MM else 136          # ~21 / 17 mm at 203 dpi
    module = max(2, min(6, target // (len(m) + 8)))
    q = qr.image(m, module=module, quiet=4)
    if not caption:
        return q
    # the caption never makes the block wider than max_w (Codex P2 on #103): smaller font, then cut with …
    limit = max(q.width, (max_w or width) - 8)
    rtl = "rtl" if shaping_available() else None
    probe = ImageDraw.Draw(q)
    for size in range(20 if width >= DOTS_80MM else 18, 13, -1):
        f = ImageFont.truetype(font_path or find_font(), size)
        if probe.textlength(caption, font=f, direction=rtl) <= limit:
            break
    while len(caption) > 1 and probe.textlength(caption, font=f, direction=rtl) > limit:
        caption = caption[:-2] + "…"
    tw = probe.textlength(caption, font=f, direction=rtl)
    w = max(q.width, int(tw) + 8)
    img = Image.new("L", (w, q.height + 26), 255)
    img.paste(q, ((w - q.width) // 2, 0))
    ImageDraw.Draw(img).text(((w - tw) / 2, q.height - 6), caption, font=f, fill=0, direction=rtl)
    return img


def receipt_header(width: int, logo_png: bytes | None = None, qr_text: str = "", qr_caption: str = "",
                   font_path: str | None = None) -> Image.Image | None:
    """Logo and/or QR code above the receipt text (F27): logo on the right and QR on the left
    (Arabic reads right to left), or either one centred. Grayscale; raster_bytes makes it 1-bit."""
    from . import logo as logo_mod
    if not logo_png and not qr_text:
        return None
    pad, gap = 8, 16
    # beside a logo the code and its caption keep to the left half; alone, the whole width
    q = qr_block(width, qr_text, qr_caption, font_path,
                 max_w=(width // 2 - pad) if logo_png else width - 2 * pad) if qr_text else None
    lg = None
    if logo_png:
        room = max(1, width - 2 * pad - ((q.width + gap) if q else 0))
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
    b = qr_block(width, text, caption, font_path, max_w=width - 16)
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
        raise PrinterError("لم تُختر طابعة الإيصال (البرنامج ← طابعة الإيصال والدرج)")
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


# #98: how long a Windows job may wait in the queue before the printer counts as not answering
SPOOL_WAIT = 6.0
SPOOL_POLL = 0.25
_JOB_STUCK = 0x2 | 0x20 | 0x40 | 0x200 | 0x400 | 0x1   # ERROR, OFFLINE, PAPEROUT, BLOCKED_DEVQ, USER_INTERVENTION, PAUSED
_JOB_PRINTING = 0x10
_JOB_DONE = 0x80 | 0x1000   # PRINTED, COMPLETE: finished but kept in the queue ("Keep printed documents")


def _send_windows(printer_name: str, data: bytes, wait: float | None = None, clock=time.monotonic,
                  sleep=time.sleep) -> None:
    """RAW job to a Windows queue, then make sure the printer took it (#98). Windows keeps a job for a printer
    that is switched off and reports the printer as ready; the job (and its drawer pulse) would then fire by
    itself when the printer comes back. So: wait until the job has left the queue, or is printing with no
    error, or is marked printed/complete (a queue set to keep printed documents holds finished jobs).
    Otherwise cancel it and raise PrinterError, so the screen warns and nothing fires later."""
    try:
        import win32print  # type: ignore
    except ImportError as e:
        raise PrinterError("الطباعة على Windows تحتاج إلى تثبيت pywin32 (pip install pywin32)") from e
    wait = SPOOL_WAIT if wait is None else wait
    h = win32print.OpenPrinter(printer_name)
    try:
        job = win32print.StartDocPrinter(h, 1, ("pharmacy-print", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
        await_job(win32print, h, job, wait, clock, sleep)
    finally:
        win32print.ClosePrinter(h)


def await_job(win32print, h, job: int, wait: float, clock=time.monotonic, sleep=time.sleep) -> None:
    """#98: the job has left the queue, or is printing (or printed) with no error, within `wait` seconds;
    otherwise it is cancelled and PrinterError raised. Shared by the RAW queue and the driver labels (#100)."""
    end = clock() + wait
    while True:
        status = _job_status(win32print, h, job)
        if status is None or (status & (_JOB_PRINTING | _JOB_DONE) and not status & _JOB_STUCK):
            return
        if clock() >= end:
            break
        sleep(SPOOL_POLL)
    try:
        win32print.SetJob(h, job, 0, None, win32print.JOB_CONTROL_DELETE)
        cancelled = True
    except Exception:  # noqa: BLE001 — pywintypes.error; reported below
        cancelled = False
    if cancelled:
        raise PrinterError("الطابعة لا تستجيب (مطفأة أو غير موصلة أو نفد الورق) — أُلغي أمر الطباعة، فلن يُطبع لاحقًا من تلقاء نفسه")
    raise PrinterError("الطابعة لا تستجيب، وتعذر إلغاء أمر الطباعة من ويندوز: امسحه من قائمة الطباعة قبل تشغيل الطابعة")


def _job_status(win32print, h, job: int) -> int | None:
    """The job's status bits, or None once it has left the queue. If Windows will not list the queue,
    None too: the job was handed over and the check is skipped (the behaviour before #98)."""
    try:
        jobs = win32print.EnumJobs(h, 0, 999, 1)
    except Exception:  # noqa: BLE001 — pywintypes.error
        return None
    for j in jobs:
        if j.get("JobId") == job:
            return int(j.get("Status") or 0)
    return None


def _unit(name: str | None, default: str) -> str:
    return name if name not in (None, "", "unit", "pack") else default


def qty_parts(l: dict) -> tuple[int, int, str, str]:
    """(boxes, loose units, box name, unit name) of a sold line: the unit it was sold in (U2-24), never only
    the base units («6 شريط» for one box)."""
    it, q = l["item"], l["qty_base"]
    ps = max(1, it.get("pack_size") or 1)
    pack, unit = _unit(it.get("pack_unit"), "علبة"), _unit(it.get("base_unit"), "وحدة")
    return (q // ps, q % ps, pack, unit) if ps > 1 else (0, q, pack, unit)


def qty_text(l: dict) -> str:
    b, u, pack, unit = qty_parts(l)
    return " + ".join(x for x in (f"{b} {pack}" if b else "", f"{u} {unit}" if u else "") if x) or "0"


def unit_price(l: dict) -> int | None:
    """Price before discount of one sold unit (a box when sold in whole boxes), whole piastres half up. None when
    the line left from batches at different public prices: an average was never charged (Codex P1 on #423), so the
    receipt shows the quantity and the line total only."""
    b, u, _, _ = qty_parts(l)
    gross = l.get("gross")
    if gross is None:
        return None
    packs = {(a.get("pack_price"), a.get("pack_size")) for a in l.get("allocations") or []
             if a.get("pack_price") is not None}
    if len(packs) > 1:
        return None
    n = b if b and not u else (b * max(1, l["item"].get("pack_size") or 1) + u)
    return (2 * gross + n) // (2 * n) if n else None


def public_price_rows(line: dict) -> list[tuple[str, int | None, int]]:
    """Receipt rows share saved allocation prices across browser and thermal rendering."""
    _, _, pack, unit = qty_parts(line)
    return [(f"{row['qty']} {pack if row['pack'] else unit}", row["price"], row["gross"])
            for row in line.get("official_prices", [])] or [
                (qty_text(line), unit_price(line), line.get("gross", line["line_total"]))]


def _spread(line: dict, rows: list[tuple[str, int | None, int]], off: int) -> list[tuple[str, int | None, int]]:
    """UX-26: a hidden discount taken off the line's rows by their share of the gross (whole piastres, the rest on
    the last row), so the printed rows still add up to what was paid. The price shown is the net one only when it
    divides exactly, else none (never a rounded price)."""
    total = sum(g for _, _, g in rows)
    counts = [r["qty"] for r in line.get("official_prices") or []]
    if len(counts) != len(rows):
        b, u, _, _ = qty_parts(line)
        counts = [b if b and not u else b * max(1, line["item"].get("pack_size") or 1) + u]
    out, left = [], off
    for i, (qty, _, gross) in enumerate(rows):
        cut = left if i == len(rows) - 1 else (off * gross // total if total else 0)
        left -= cut
        net = gross - cut
        n = counts[i] if i < len(counts) else 0
        out.append((qty, net // n if n and net % n == 0 else None, net))
    return out


def discount_view(sale: dict, cfg: dict) -> dict:
    """UX-26 (owner 26/11): what the receipt shows of the discounts. A hidden kind is folded into the lines, so the
    customer sees net amounts that add up to the same final total. item/invoice = None when hidden."""
    show_item, show_inv = cfg.get("show_item_discount", True), cfg.get("show_invoice_discount", True)
    lines, hidden = [], 0
    for line in sale["lines"]:
        inv = line.get("invoice_share") or 0
        item = (line["discount"] - inv if line.get("discount") is not None else line.get("discount_amount") or 0)
        off = (0 if show_item else item) + (0 if show_inv else inv)
        rows = public_price_rows(line)
        lines.append((line, _spread(line, rows, off) if off else rows, item if show_item else 0))
        hidden += off
    inv_total = sum(line.get("invoice_share") or 0 for line in sale["lines"])
    item_total = sale["discount_total"] - inv_total
    shown = (item_total if show_item else 0) + (inv_total if show_inv else 0)
    return {"lines": lines, "subtotal": (sale.get("subtotal") or 0) - hidden,
            "item": item_total if show_item else None, "invoice": inv_total if show_inv else None, "shown": shown}


def policy_text(n: int, restricted: bool) -> str:
    """The return-policy line, the same on the thermal and the browser receipt (B1)."""
    if n <= 0:
        text = "المرتجع غير متاح بهذه الفاتورة"
    else:
        days = {1: "يوم واحد", 2: "يومين"}.get(n) or (f"{n} أيام" if 3 <= n <= 10 else f"{n} يومًا")
        text = f"المرتجع خلال {days} بهذه الفاتورة"
    return text + (" · الأصناف التي عليها ✱ لا تُقبل إعادتها ولا استبدالها" if restricted else "")


def _receipt_amount(piasters: int) -> str:
    pounds, remainder = divmod(abs(piasters), 100)
    return f"{'-' if piasters < 0 else ''}{pounds}.{remainder:02d}"


def receipt_lines(org_name: str, sale: dict, when: str, cfg: dict | None = None, *, seller: str = "",
                  cashier: str = "", given: int = 0, return_days: int | None = None) -> list[ReceiptRow]:
    """Build the thermal receipt from the saved sale and its public-price allocations."""
    cfg = cfg or {}
    amount = _receipt_amount
    out = [("title", cfg.get("title") or org_name, "")]
    out.extend(("center", ln, "") for ln in cfg.get("lines") or () if ln)
    if cfg.get("header"):
        out.append(("center", cfg["header"], ""))
    for key, label in (("tax_no", "رقم التسجيل الضريبي"), ("cr_no", "السجل التجاري")):
        if cfg.get(key):
            out.append(("small", f"{label}: {cfg[key]}", ""))
    out.extend([("rule", "", ""), ("small", f"فاتورة رقم {sale['sale_no']}", when)])
    if cfg.get("show_cashier", True) and (seller or cashier):
        # UX-26 (owner 26/11): one name, the person who made the sale (the one who completed it if unknown)
        out.append(("small", f"البائع: {seller or cashier}", ""))

    customer, delivery = sale.get("customer") or {}, sale.get("delivery") or {}
    is_delivery = sale.get("kind") == "delivery"
    is_credit = bool(sale.get("on_credit"))
    if (customer and cfg.get("show_customer", True)) or is_delivery or is_credit:
        if customer.get("name"):
            code = customer.get("estock_code")
            suffix = f" (كود {code})" if code is not None else ""
            out.append(("customer", f"العميل: {customer['name']}{suffix}", ""))
        phone = delivery.get("phone") or customer.get("phone")
        address = delivery.get("address") or customer.get("address1")
        if phone:
            out.append(("customer", f"موبايل: {phone}", ""))
        if address and (is_delivery or is_credit):
            out.append(("customer", f"العنوان: {address}", ""))

    method_names = {"cash": "كاش", "card": "فيزا", "transfer": "محفظة", "credit": "آجل"}
    pays = sale.get("payments") or [
        {"method": method, "amount": sale[key]}
        for key, method in (("paid_cash", "cash"), ("paid_card", "card"),
                            ("paid_transfer", "transfer"), ("on_credit", "credit"))
        if sale.get(key)
    ]
    methods = list(dict.fromkeys(method_names.get(p["method"], p["method"]) for p in pays))
    payment_type = "توصيل" if is_delivery else " / ".join(methods)
    if payment_type:
        out.append(("small", f"الدفع: {payment_type}", ""))
    out.extend([("rule", "", ""), ("columns", "الصنف", ("م", "الكمية", "السعر", "الإجمالي"))])

    lines = sale["lines"]
    view = discount_view(sale, cfg)
    number = pieces = 0
    for line, prices, line_discount in view["lines"]:
        item = line["item"]
        if item.get("hide_on_receipt"):
            name = "—"
        elif cfg.get("names") == "both":
            name = " / ".join(dict.fromkeys(n for n in (item.get("name_en"), item.get("name_ar")) if n))
        else:
            name = item.get("name_ar" if cfg.get("names") == "ar" else "name_en")
        name = upper_en(name or item["name"]) + (" ✱" if item.get("no_return") else "")
        for qty, price, gross in prices:
            number += 1
            out.append(("item", name, (str(number), qty, amount(price) if price is not None else "—", amount(gross))))
        saved_prices = line.get("official_prices")
        if saved_prices:
            pieces += sum(p["qty"] for p in saved_prices)
        else:
            boxes, loose, _, _ = qty_parts(line)
            pieces += boxes + loose
        if cfg.get("show_expiry"):
            dates = sorted({a["expiry_date"][:7] for a in line.get("allocations") or () if a.get("expiry_date")})
            if dates:
                out.append(("detail", f"الصلاحية: {' / '.join(dates)}", ""))
        bp = line.get("discount_bp") or 0
        if line_discount > 0 and cfg.get("template") != "compact":
            percent = f" {amount(bp).rstrip('0').rstrip('.')}%" if bp else ""
            out.append(("detail", f"خصم{percent} −{amount(line_discount)}", ""))

    out.append(("rule", "", ""))
    if lines and cfg.get("show_item_count", True):
        kinds = {line.get("item_id") or i for i, line in enumerate(lines)}
        out.append(("small", f"عدد الأصناف: {len(kinds)}", ""))
    if lines:
        out.append(("small", f"عدد القطع: {pieces}", ""))
    if sale.get("subtotal") is not None:
        out.append(("row", "الإجمالي قبل الخصم", amount(view["subtotal"])))
    if view["item"] is not None:
        out.append(("row", "خصم الأصناف", amount(view["item"])))
    if view["invoice"] is not None:
        out.append(("row", "خصم الفاتورة", amount(view["invoice"])))
    total_discount = view["shown"]
    if sale.get("delivery_fee"):
        out.append(("row", "رسوم التوصيل", amount(sale["delivery_fee"])))
    rounding = sale.get("rounding") or 0
    if rounding and cfg.get("show_rounding", False):      # UX-26: hidden by default; the total is the final amount
        out.append(("row", "التقريب", ("+" if rounding > 0 else "") + amount(rounding)))
    out.extend([("rule", "", ""), ("total", "المطلوب", amount(sale["total"] + rounding)), ("rule", "", "")])
    for p in pays:
        label = method_names.get(p["method"], p["method"])
        channel = f" · {p['channel']}" if p.get("channel") else ""
        if p["method"] == "cash" and is_delivery and delivery.get("status") not in ("delivered", "settled"):
            label = "كاش عند الاستلام"
        elif p["method"] != "credit":
            label = "مدفوع " + label
        out.append(("row", label + channel, amount(p["amount"])))
    cash = sale.get("paid_cash") or 0
    if cfg.get("show_change", True) and cash and given > cash:
        out.extend([("row", "المدفوع من العميل", amount(given)), ("total", "الباقي", amount(given - cash))])
    if total_discount > 0:
        out.append(("center", f"وفّرت: {amount(total_discount)} ج", ""))
    if is_credit:
        out.append(("center", "هذه الفاتورة على الحساب", ""))
        # Sales.get provides the invoice balances only while the account is unchanged.
        if customer.get("balance_before") is not None and customer.get("balance") is not None:
            out.extend([("row", "الرصيد السابق", amount(customer["balance_before"])),
                        ("row", "الرصيد الحالي", amount(customer["balance"]))])

    out.append(("rule", "", ""))
    if return_days is not None and cfg.get("show_return_policy", True):
        restricted = any(line["item"].get("no_return") for line in lines)
        out.extend(("small", part, "") for part in policy_text(return_days, restricted).split(" · "))
    footer = (cfg.get("footer") or "").removeprefix("شكرًا لزيارتكم — ")
    if footer and footer != "شكرًا لزيارتكم":
        out.append(("center", footer, ""))
    out.extend([("barcode", str(sale["sale_no"]), ""), ("footer_codes", "", ""),
                ("center", "شكرًا لزيارتكم", "")])
    return out
