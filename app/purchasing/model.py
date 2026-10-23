"""Extraction result model + robust value parsers shared by all importers.

Every extracted field carries a *source* so the UI can tell the pharmacist
where a value came from (spec §6):
  read     - read directly from the document
  inferred - computed/derived by the system (e.g. net price from public price
             and discount) or produced by an AI model
  db       - taken from the pharmacy database
  user     - typed or confirmed by a person
and a confidence in [0, 1].
"""
from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")

LINE_FIELDS = ("raw_name", "raw_code", "raw_barcode", "qty", "bonus_qty", "public_price",
               "discount_pct", "unit_net_price", "tax_amount", "line_total", "batch_no",
               "expiry_date")


@dataclass
class ExtractedLine:
    raw_name: str
    raw_code: str | None = None
    raw_barcode: str | None = None
    qty: int | None = None
    bonus_qty: int | None = None
    public_price: Decimal | None = None     # EGP per pack
    discount_pct: Decimal | None = None     # 12.5 means 12.5 %
    unit_net_price: Decimal | None = None   # EGP per pack after discount
    tax_amount: Decimal | None = None       # EGP for the line
    line_total: Decimal | None = None       # EGP
    batch_no: str | None = None
    expiry_date: str | None = None          # ISO date
    expiry_precision: str | None = None
    sources: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)


@dataclass
class ExtractedInvoice:
    method: str                              # structured | pdf_text | ai_vision
    supplier_name: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    declared_total: Decimal | None = None
    extra_discount: Decimal | None = None
    extra_tax: Decimal | None = None
    lines: list[ExtractedLine] = field(default_factory=list)
    model: str | None = None
    warnings: list[str] = field(default_factory=list)
    header_sources: dict = field(default_factory=dict)
    header_confidence: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        def conv(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return conv(asdict(self))


# ------------------------------------------------------------------ parsers
_CURRENCY = re.compile(r"(ج\.?\s?م\.?|جنيه|egp|l\.?e\.?|le)", re.I)


def _clean_num(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    s = str(v).translate(_AR_DIGITS).strip()
    s = _CURRENCY.sub("", s).replace(" ", "").replace("\u00a0", "")
    if not s or s in "-.,":
        return None
    if re.search(r"[^\d.,\-%]", s):
        return None          # letters mixed in (e.g. '1O', '12a'): unreadable, never guess
    s = s.replace("%", "")
    if "," in s and "." in s:
        s = s.replace(",", "")                          # 1,234.50
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = s.replace(",", "") if len(tail) == 3 else head.replace(",", "") + "." + tail
    return s


def parse_decimal(v) -> Decimal | None:
    s = _clean_num(v)
    if s is None:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def parse_int(v) -> int | None:
    d = parse_decimal(v)
    if d is None:
        return None
    if d != d.to_integral_value():
        return None          # 2.5 boxes is not a valid pack quantity: leave for review
    return int(d)


def parse_percent(v) -> Decimal | None:
    """'12%' -> 12, '12.5' -> 12.5. Fractions like 0.12 are NOT guessed here;
    the caller decides at column level (see normalize_percent_column)."""
    d = parse_decimal(v)
    if d is None or d < 0 or d > 100:
        return None
    return d


def normalize_percent_column(values: list[Decimal | None]) -> tuple[list[Decimal | None], bool]:
    """If every non-empty value in a discount column is <= 1, the sheet stores
    fractions (0.12) -> convert to percent. Returns (values, converted)."""
    present = [v for v in values if v is not None]
    if present and all(v <= 1 for v in present) and any(v > 0 for v in present):
        return [v * 100 if v is not None else None for v in values], True
    return values, False


def to_piasters(d: Decimal | None) -> int | None:
    if d is None:
        return None
    return int((d * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def pct_to_bp(d: Decimal | None) -> int | None:
    if d is None:
        return None
    return int((d * 100).quantize(Decimal("1"), ROUND_HALF_UP))


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})


def _year(y: int) -> int:
    return 2000 + y if y < 100 else y


def _month_end(y: int, m: int) -> str:
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def parse_expiry(v) -> tuple[str | None, str | None]:
    """Returns (iso_date, precision). Accepts '05/2027', '5-27', '2027-05',
    '31/05/2027', 'MAY-27', 'May 2027', Excel serials and datetime objects.
    Ambiguous or invalid input returns (None, None) — never a guess."""
    if v is None or v == "":
        return None, None
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.isoformat(), "day"
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:   # Excel serial
        d = date(1899, 12, 30) + timedelta(days=int(v))
        return d.isoformat(), "day"
    s = str(v).translate(_AR_DIGITS).strip().lower()
    s = re.sub(r"^(exp|expiry|ex|صلاحيه|صلاحية|ينتهي)[\s.:]*", "", s)
    try:
        m = re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{2}|\d{4})", s)              # MM/YY(YY)
        if m:
            mm, yy = int(m.group(1)), _year(int(m.group(2)))
            if 1 <= mm <= 12:
                return _month_end(yy, mm), "month"
            return None, None
        m = re.fullmatch(r"(\d{4})\s*[/\-.]\s*(\d{1,2})", s)                   # YYYY-MM
        if m:
            yy, mm = int(m.group(1)), int(m.group(2))
            return (_month_end(yy, mm), "month") if 1 <= mm <= 12 else (None, None)
        m = re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2}|\d{4})", s)  # DD/MM/YYYY
        if m:
            dd, mm, yy = int(m.group(1)), int(m.group(2)), _year(int(m.group(3)))
            return date(yy, mm, dd).isoformat(), "day"
        m = re.fullmatch(r"(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})", s)   # YYYY-MM-DD
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), "day"
        m = re.fullmatch(r"([a-z]{3,9})\s*[/\-. ]\s*(\d{2}|\d{4})", s)          # MAY-27
        if m and m.group(1) in _MONTHS:
            return _month_end(_year(int(m.group(2))), _MONTHS[m.group(1)]), "month"
    except ValueError:
        return None, None
    return None, None


def parse_date(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    iso, precision = parse_expiry(v)
    return iso if precision == "day" else None
