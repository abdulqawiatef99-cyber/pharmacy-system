"""Reading the 2D code (GS1 DataMatrix / QR) printed on medicine packs.

A 2D scanner types the code like a keyboard. Two shapes exist:
- Element string: 01<GTIN-14> 17<YYMMDD expiry> 10<batch> 21<serial>, variable fields
  separated by the GS character (often lost by keyboard scanners; then we use the usual
  field order and lengths as a best effort).
- GS1 Digital Link URL: https://<any host>/01/<GTIN>/10/<batch>/21/<serial>?17=<YYMMDD>
Nothing here is guessed silently: the caller shows what was read.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from urllib.parse import parse_qs, unquote, urlparse

GS = "\x1d"
_PREFIX = re.compile(r"^(\]d2|\]Q3|\]C1)")


def _expiry(yymmdd: str) -> str | None:
    if not re.fullmatch(r"\d{6}", yymmdd):
        return None
    y, m, d = 2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    if not 1 <= m <= 12:
        return None
    if d == 0:                                   # GS1: day 00 = end of month
        d = calendar.monthrange(y, m)[1]
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def gtin_keys(gtin: str) -> list[str]:
    """A GTIN-14 starting with 0 is the EAN-13 printed as a normal barcode."""
    keys = [gtin]
    while len(keys[-1]) > 8 and keys[-1].startswith("0"):
        keys.append(keys[-1][1:])
    return keys


def _split_without_gs(body: str, ai: str, have_expiry: bool) -> int:
    """Where a variable field (10 batch / 21 serial) ends when the scanner dropped the GS separator.

    Best effort, in this order: while no expiry was read yet, a 17<YYMMDD> that is a real date and is
    followed by the end or by the other variable field (a batch or serial that happens to contain "21" is
    not cut there); then, for a batch only, the first "21" (serial); otherwise the field runs to the end.
    Once the code carried its expiry, digits like 17<YYMMDD> inside a later field are that field's own.
    """
    other = "21" if ai == "10" else "10"
    for m in ([] if have_expiry else re.finditer(r"17(\d{6})", body)):
        if m.start() >= 1 and _expiry(m.group(1)) and (m.end() == len(body) or body.startswith(other, m.end())):
            return m.start()
    if ai == "10":
        i = body.find("21", 1)
        if 0 < i and i + 2 < len(body):              # a "21" at the very end is part of the batch, not an empty serial
            return i
    return len(body)


def parse(raw: str) -> dict | None:
    """{'gtin', 'expiry', 'batch', 'serial'} or None if this is not a GS1 2D code."""
    s = (raw or "").strip()
    if not s:
        return None
    s = _PREFIX.sub("", s)
    if s.lower().startswith(("http://", "https://")):
        u = urlparse(s)
        parts = [unquote(p) for p in u.path.split("/") if p]
        out: dict = {}
        for i in range(len(parts) - 1):
            if parts[i] == "01" and re.fullmatch(r"\d{8,14}", parts[i + 1]):
                out["gtin"] = parts[i + 1].zfill(14)
            elif parts[i] == "10":
                out["batch"] = parts[i + 1]
            elif parts[i] == "21":
                out["serial"] = parts[i + 1]
        q = parse_qs(u.query)
        if "17" in q:
            out["expiry"] = _expiry(q["17"][0])
        return out if "gtin" in out else None
    s = re.sub(r"\((\d{2})\)", GS + r"\1", s)       # "(01)...(17)..." as printed under the code
    s = s.lstrip(GS)
    if not re.match(r"^01\d{14}", s):
        return None
    out = {"gtin": s[2:16]}
    rest = s[16:]
    while rest:
        rest = rest.lstrip(GS)
        ai = rest[:2]
        if ai in ("17", "11", "15") and re.match(r"^\d{8}", rest):
            if ai == "17":
                out["expiry"] = _expiry(rest[2:8])
            rest = rest[8:]
        elif ai in ("10", "21"):
            body = rest[2:]
            if GS in body:
                val, rest = body.split(GS, 1)
            else:
                cut = _split_without_gs(body, ai, "expiry" in out)
                val, rest = body[:cut], body[cut:]
            out["batch" if ai == "10" else "serial"] = val[:20]
        else:
            break
    return out
