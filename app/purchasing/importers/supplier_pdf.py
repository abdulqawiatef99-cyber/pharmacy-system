"""Coordinate-based reader for the Arabic warehouse/customer invoice layout."""
from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, field

from ...catalog.normalize import normalize_text
from ..model import ExtractedInvoice, parse_date, parse_decimal, parse_expiry, parse_int
from .table import grid_to_invoice

# Match whole Arabic phrases, leaving numeric, Latin and punctuation runs in place.
_ARABIC_LETTER = r"[\u0621-\u063a\u0640-\u065f\u066e-\u06d3\ufb50-\ufdff\ufe70-\ufefc]"
_ARABIC_RUN = re.compile(rf"{_ARABIC_LETTER}+(?: +{_ARABIC_LETTER}+)*")
_PRESENTATION = re.compile(r"[\ufb50-\ufdff\ufe70-\ufefc]")
_FONT_LETTERS = str.maketrans({"ھ": "ه", "ی": "ي"})
_COLUMNS = {
    "م": "serial", "الموقع": "location", "الصنف": "name", "الكميه": "qty",
    "السعر": "price", "الخصم": "discount", "الاجمالي": "total",
}
_REQUIRED = set(_COLUMNS.values())
_DETAILS = {"صلاحيه", "الصلاحيه", "تشغيله", "التشغيله"}
_GRID_HEADER = ["الصنف", "الكمية", "السعر", "الخصم", "الاجمالى", "التشغيلة", "الصلاحية"]
_GRID_COLUMNS = ["name", "qty", "price", "discount", "total", "batch", "expiry"]
_META_LABELS = {
    "invoice_no": r"رقم الفاتور[ةه]",
    "invoice_date": r"(?:تاريخ الفاتور[ةه]|التاريخ)",
    "declared_total": r"صاف[ىي] الفاتور[ةه]",
}
_META_VALUES = {
    "invoice_no": r"[A-Za-z0-9٠-٩۰-۹][A-Za-z0-9٠-٩۰-۹/-]{0,30}",
    "invoice_date": r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}",
    "declared_total": r"\d[\d,.٫٬]*",
}
def _metadata_patterns(label: str, value: str) -> tuple[re.Pattern, ...]:
    # A marked label/value pair takes precedence over text from the adjacent field.
    return tuple(re.compile(pattern) for pattern in (
        rf"{label}\s*[:：]\s*({value})(?!\S)",
        rf"(?<!\S)({value})\s*[:：]\s*{label}",
        rf"{label}\s+({value})(?!\S)",
        rf"(?<!\S)({value})\s+{label}",
    ))


_METADATA = {key: _metadata_patterns(label, _META_VALUES[key]) for key, label in _META_LABELS.items()}
_DAY_EXPIRY = re.compile(r"(?<!\w)\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}(?!\w)")
_BATCH_LABEL = re.compile(r"(?:ال)?تشغيل[ةه]\s*[:：]?\s*")
_EXPIRY_LABEL = re.compile(r"(?:ال)?صلاحي[ةه]\s*[:：]?\s*")


def _logical(text: str, visual: bool) -> str:
    if visual:
        # Reverse glyphs before expanding ligatures such as lam-alef via NFKC.
        text = _ARABIC_RUN.sub(lambda match: match.group()[::-1], text)
    text = unicodedata.normalize("NFKC", text).replace("ـ", "").strip()
    # The invoice font uses Persian glyphs to represent Arabic heh and yeh.
    return text.translate(_FONT_LETTERS) if visual else text


def _center(word) -> float:
    return (word["x0"] + word["x1"]) / 2


def _text(words, visual: bool) -> str:
    return _logical(" ".join(word["text"] for word in words), visual)


def _rows(page):
    from pdfplumber.utils import cluster_objects

    words = page.extract_words(x_tolerance=1.5)
    return [sorted(row, key=lambda word: word["x0"]) for row in cluster_objects(words, "top", 3)]


@dataclass
class _Header:
    index: int
    fields: list[str]
    boundaries: list[float]
    visual: bool

    @property
    def has_details(self) -> bool:
        return "details" in self.fields


def _find_header(rows) -> _Header | None:
    for index, row in enumerate(rows):
        visual = any(_PRESENTATION.search(word["text"]) for word in row)
        columns = {}
        detail_positions = []
        for word in row:
            label = normalize_text(_logical(word["text"], visual))
            if label in _COLUMNS:
                columns[_COLUMNS[label]] = _center(word)
            if any(part.strip() in _DETAILS for part in label.split("/")):
                detail_positions.append(_center(word))
        if columns.keys() != _REQUIRED:
            continue
        # The stacked or slash-separated detail heading sits beyond the total column.
        total, price = columns["total"], columns["price"]
        if index + 1 < len(rows):
            following = rows[index + 1]
            if following[0]["top"] - row[0]["top"] <= 14:
                for word in following:
                    label = normalize_text(_logical(word["text"], visual))
                    if label in _DETAILS:
                        detail_positions.append(_center(word))
        detail_positions = [x for x in detail_positions if (x - total) * (price - total) < 0]
        if detail_positions:
            columns["details"] = sum(detail_positions) / len(detail_positions)
        ordered = sorted(columns.items(), key=lambda pair: pair[1])
        fields, centers = zip(*ordered)
        boundaries = [(left + right) / 2 for left, right in zip(centers, centers[1:])]
        return _Header(index, list(fields), boundaries, visual)
    return None


def _cells(row, header: _Header) -> dict[str, str]:
    buckets = {field: [] for field in header.fields}
    for word in row:
        field = header.fields[bisect_right(header.boundaries, _center(word))]
        buckets[field].append(word)
    return {field: _text(words, header.visual) for field, words in buckets.items() if words}


def _read_details(text: str, line: dict[str, str]) -> None:
    if normalize_text(text) in _DETAILS:
        return
    unlabelled = _EXPIRY_LABEL.sub("", text).strip(" /:-")
    if (unlabelled != text or "batch" in line) and parse_expiry(unlabelled)[0]:
        line.setdefault("expiry", unlabelled)
        return
    expiry = _DAY_EXPIRY.search(text)
    if expiry:
        line.setdefault("expiry", expiry.group())
        text = text[:expiry.start()] + text[expiry.end():]
    batch = _EXPIRY_LABEL.sub("", _BATCH_LABEL.sub("", text)).strip(" /:-")
    if batch:
        line.setdefault("batch", batch)


@dataclass
class _PdfInvoice(ExtractedInvoice):
    # The purchasing model has no payment field. Keep the document's term in its raw extraction.
    payment_method: str | None = None


@dataclass
class SupplierPdf:
    """Read one consistent copy across the pages, reusing its columns on continuation pages."""

    header: _Header | None = None
    side: int | None = None
    size: tuple[float, float] | None = None
    lines: list[dict[str, str]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    header_texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_page(self, page) -> bool:
        left, top, right, bottom = page.bbox
        midpoint = (left + right) / 2
        halves = [(left, top, midpoint, bottom), (midpoint, top, right, bottom)]
        halves_rows = [_rows(page.crop(box)) for box in halves]
        half_headers = [_find_header(rows) for rows in halves_rows]
        candidates = [side for side, header in enumerate(half_headers) if header is not None]
        if len(candidates) == 2 or any(half_headers[side].has_details for side in candidates):
            self.side = max(candidates, key=lambda side: (half_headers[side].has_details, side == self.side, side))
            rows, header = halves_rows[self.side], half_headers[self.side]
        elif self.header and self.size == (page.width, page.height) and self.side is not None:
            rows, header = halves_rows[self.side], half_headers[self.side]
        else:
            rows = _rows(page)
            header = _find_header(rows)
            if header:
                self.side = None
        if header:
            self.header = header
            self.size = (page.width, page.height)
        elif not self.header or self.size != (page.width, page.height):
            return False
        self._read_page(rows, start=header.index + 1 if header else 0)
        return True

    def _read_page(self, rows, *, start: int) -> None:
        header = self.header
        line = None
        last_top = 0
        for index, row in enumerate(rows):
            text = _text(row, header.visual)
            if self._read_metadata(text, before_table=index < start) or index < start:
                line = None
                continue

            cells = _cells(row, header)
            serial = parse_int(cells.get("serial"))
            top = row[0]["top"]
            if serial is not None and serial > 0:
                line = {key: value for key, value in cells.items() if key in _GRID_COLUMNS}
                self.lines.append(line)
            elif line is not None and top - last_top <= 3 * max(word["height"] for word in row):
                if name := cells.get("name"):
                    line["name"] = " ".join(filter(None, (line.get("name"), name)))
                for key in ("qty", "price", "discount", "total"):
                    if key in cells:
                        line.setdefault(key, cells[key])
            else:
                line = None
            if line is not None:
                _read_details(cells.get("details", ""), line)
                last_top = top

    def _read_metadata(self, text: str, *, before_table: bool) -> bool:
        recognized = False
        for key, patterns in _METADATA.items():
            if match := next((match for pattern in patterns if (match := pattern.search(text))), None):
                raw = match[1]
                value = {"invoice_date": parse_date, "declared_total": parse_decimal}.get(key, str)(raw)
                if value is not None:
                    self.metadata.setdefault(key, value)
                recognized = True
        payment = normalize_text(text)
        if payment == "شهري" or (before_table and "شهري" in payment.split()):
            self.metadata.setdefault("payment_method", "credit")
            recognized = True
        if before_table:
            self.header_texts.append(text)
            if re.match(r"^(?:مخزن|شركة|شركه|مستودع)\s+\S", text):
                self.metadata.setdefault("supplier_name", text)
            if re.fullmatch(_META_VALUES["invoice_date"], text) and (date := parse_date(text)):
                self.metadata.setdefault("invoice_date", date)
        return recognized

    def invoice(self) -> ExtractedInvoice:
        rows = [_GRID_HEADER] + [[line.get(key) for key in _GRID_COLUMNS] for line in self.lines]
        parsed = grid_to_invoice(rows, "pdf_text")
        payment = self.metadata.get("payment_method")
        result = _PdfInvoice(**vars(parsed), payment_method=payment)
        result.header_texts = self.header_texts[:40]
        for key, value in self.metadata.items():
            setattr(result, key, value)
            result.header_sources[key] = "read"
            result.header_confidence[key] = 0.9
        if len(result.lines) != len(self.lines):
            result.warnings.append("unparsed:pdf_item")
        return result
