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
import socket
import time
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
    # a small row with no amount (expiry, discount, the return policy) wraps by words instead of being cut with «…»:
    # the ✱ rule at the end of the policy was lost on thermal paper (Codex P2 on #423)
    meas, wrapped = ImageDraw.Draw(Image.new("L", (1, 1), 255)), []
    for kind, right, left in lines:
        if kind != "small" or left or not right or \
                meas.textlength(right, font=fonts["small"], direction="rtl") <= width - 2 * pad:
            wrapped.append((kind, right, left))
            continue
        cur = ""
        for word in right.split(" "):
            nxt = f"{cur} {word}".strip()
            if cur and meas.textlength(nxt, font=fonts["small"], direction="rtl") > width - 2 * pad:
                wrapped.append(("small", cur, ""))
                cur = word
            else:
                cur = nxt
        if cur:
            wrapped.append(("small", cur, ""))
    lines = wrapped
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


def policy_text(n: int, restricted: bool) -> str:
    """The return-policy line, the same on the thermal and the browser receipt (B1)."""
    if n <= 0:
        text = "المرتجع غير متاح بهذه الفاتورة"
    else:
        days = {1: "يوم واحد", 2: "يومين"}.get(n) or (f"{n} أيام" if 3 <= n <= 10 else f"{n} يومًا")
        text = f"المرتجع خلال {days} بهذه الفاتورة"
    return text + (" · الأصناف التي عليها ✱ لا تُقبل إعادتها ولا استبدالها" if restricted else "")


def receipt_lines(org_name: str, sale: dict, when: str, cfg: dict | None = None, *, cashier: str = "",
                  given: int = 0, return_days: int | None = None) -> list[tuple[str, str, str]]:
    """given: what the customer handed over in cash (piastres), printed with the change due when it is
    more than the cash part of the sale (F27). Shown only on the receipt printed at checkout; it is
    not stored, so a later reprint has no change line.
    U2-23 / U2-24: the sold unit and its price, each line's discount, the customer (and for credit or delivery his
    phone and address), each payment method, the customer's balance on a credit sale, the total quantity and the
    return policy line."""
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
    c, d = sale.get("customer"), sale.get("delivery") or {}
    if c and cfg.get("show_customer", True):
        out.append(("small", f"العميل: {c.get('name', '')}" + (f" (كود {c['estock_code']})" if c.get("estock_code") is not None else ""), ""))
        if sale.get("on_credit") or sale.get("kind") == "delivery":
            phone = d.get("phone") or c.get("phone")
            addr = d.get("address") or c.get("address1")
            if phone:
                out.append(("small", f"موبايل: {phone}", ""))
            if addr:
                out.append(("small", f"العنوان: {addr}", ""))
    elif sale.get("kind") == "delivery" and (d.get("address") or d.get("phone")):
        out.append(("small", "توصيل: " + " ".join(x for x in (d.get("address"), d.get("phone")) if x), ""))
    out.append(("rule", "", ""))
    boxes = units = 0
    for l in sale["lines"]:
        it = l["item"]
        name = "—" if it.get("hide_on_receipt") else (it.get("name_ar") if cfg.get("names") == "ar" else it.get("name_en")) or it["name"]
        out.append(("row", name + (" ✱" if it.get("no_return") else ""), m(l["line_total"])))
        up = unit_price(l)
        exp = ""
        if cfg.get("show_expiry"):              # as on the browser receipt (B1)
            exps = sorted({(a.get("expiry_date") or "")[:7] for a in l.get("allocations") or [] if a.get("expiry_date")})
            exp = f" · صلاحية {' / '.join(exps)}" if exps else ""
        # the line's own discount only, as on the browser receipt; the invoice discount is in «خصم» below (review P2)
        disc = (f" · خصم {l['discount_bp'] / 100:g}%" if l.get("discount_bp") else
                f" · خصم {m(l['discount_amount'])}" if l.get("discount_amount") else "")
        out.append(("small", qty_text(l) + (f" × {m(up)}" if up is not None else "") + exp + disc, ""))
        b, u, _, _ = qty_parts(l)
        boxes, units = boxes + b, units + u
    out.append(("rule", "", ""))
    if cfg.get("show_item_count") and sale["lines"]:
        out.append(("small", f"عدد الأصناف: {len(sale['lines'])}", ""))
    if sale["lines"]:
        out.append(("small", "إجمالي الكمية: " + " + ".join(x for x in (f"{boxes} علبة" if boxes else "", f"{units} وحدة" if units else "") if x), ""))
    if sale.get("subtotal") is not None:    # always, as on the browser receipt (B1)
        out.append(("row", "الإجمالي", m(sale["subtotal"])))
    if sale["discount_total"]:
        out.append(("row", "خصم", m(sale["discount_total"])))
    if sale.get("delivery_fee"):
        out.append(("row", "رسوم التوصيل", m(sale["delivery_fee"])))
    if sale.get("rounding"):               # U2-14: its own line, signed
        out.append(("row", "التقريب", ("+" if sale["rounding"] > 0 else "-") + m(abs(sale["rounding"]))))
    out.append(("total", "المطلوب", m(sale["total"] + (sale.get("rounding") or 0))))   # U2-14: cash rounding on top
    MET = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل", "credit": "آجل"}
    pays = sale.get("payments") or []
    if pays:
        for p in pays:
            out.append(("row", MET.get(p["method"], p["method"]) + (f" — {p['channel']}" if p.get("channel") else ""), m(p["amount"])))
    else:
        for key, label in (("paid_cash", "نقدي"), ("paid_card", "بطاقة"), ("paid_transfer", "تحويل"), ("on_credit", "آجل")):
            if sale.get(key):
                out.append(("row", label, m(sale[key])))
    cash = sale.get("paid_cash") or 0
    if cfg.get("show_change", True) and cash and given > cash:
        out += [("row", "المدفوع من العميل", m(given)), ("total", "الباقي", m(given - cash))]
    if c and sale.get("on_credit") and c.get("balance_before") is not None:
        # only while his account has not moved since this invoice (Sales.get decides), so a reprint is never wrong
        out += [("row", "الرصيد السابق", m(c["balance_before"])), ("row", "الرصيد الحالي", m(c["balance"]))]
    out.append(("small", cfg.get("footer") or "شكرًا لزيارتكم", ""))
    if return_days is not None and cfg.get("show_return_policy", True):
        # one row per sentence: the thermal renderer cuts a long row, so the ✱ rule is never lost (Codex P2 on #423)
        for part in policy_text(return_days, any(l["item"].get("no_return") for l in sale["lines"])).split(" · "):
            out.append(("small", part, ""))
    return out
