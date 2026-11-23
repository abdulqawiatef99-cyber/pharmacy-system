"""Coordinate-based reader for the Arabic warehouse/customer invoice layout."""
from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, field

from ...catalog.normalize import normalize_text
from ..model import ExtractedInvoice, parse_date, parse_decimal, parse_expiry, parse_int, parse_percent
from .table import grid_to_invoice

# Match whole Arabic phrases, leaving numeric, Latin and punctuation runs in place.
_ARABIC_LETTER = r"[\u0621-\u063a\u0640-\u065f\u066e-\u06d3\ufb50-\ufdff\ufe70-\ufefc]"
_ARABIC_RUN = re.compile(rf"{_ARABIC_LETTER}+(?: +{_ARABIC_LETTER}+)*")
_NAME_NUMBER = r"\d+(?:[.,٫٬/-]\d+)*"
_NAME_LATIN = r"[A-Za-z][A-Za-z0-9./+-]*"
_NAME_TOKENS = re.compile(
    rf"{_ARABIC_LETTER}+|{_NAME_LATIN}(?: +(?:{_NAME_LATIN}|{_NAME_NUMBER}))*|{_NAME_NUMBER}|[^\s]"
)
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
_AMOUNT_FIELDS = ("qty", "price", "discount", "total")
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
_NET_LABEL = re.compile(_META_LABELS["declared_total"])
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


def _rtl_name(words) -> str:
    # The visual line is RTL, while each number or Latin code is already LTR.
    tokens = _NAME_TOKENS.findall(" ".join(word["text"] for word in words))
    return " ".join(_logical(token, token != "لا") for token in reversed(tokens)
                    if token.strip("ـ"))


def _rows(words):
    from pdfplumber.utils import cluster_objects

    return [sorted(row, key=lambda word: word["x0"]) for row in cluster_objects(words, "top", 3)]


def _label(word, visual: bool) -> str:
    return normalize_text(_logical(word["text"], visual))


def _visual_row(row) -> bool:
    return any(_PRESENTATION.search(word["text"]) for word in row)


def _copy_split(rows) -> float | None:
    for row in rows:
        visual = _visual_row(row)
        labels = [_COLUMNS.get(_label(word, visual)) for word in row]
        if labels.count("name") < 2:
            continue
        for i in range(1, len(labels)):
            if set(filter(None, labels[:i])) == _REQUIRED and set(filter(None, labels[i:])) == _REQUIRED:
                return (row[i - 1]["x1"] + row[i]["x0"]) / 2
    return None


@dataclass
class _Header:
    index: int
    top: float
    fields: list[str]
    boundaries: list[float]
    visual: bool
    rtl_names: bool

    @property
    def has_details(self) -> bool:
        return "details" in self.fields


def _find_header(rows) -> _Header | None:
    for index, row in enumerate(rows):
        visual = _visual_row(row)
        columns = {}
        detail_positions = []
        for word in row:
            label = _label(word, visual)
            if label in _COLUMNS:
                columns[_COLUMNS[label]] = word
            if any(part.strip() in _DETAILS for part in label.split("/")):
                detail_positions.append(_center(word))
        if columns.keys() != _REQUIRED:
            continue

        # The detail heading can share the main baseline or occupy two baselines.
        for following in rows[index + 1:]:
            if following[0]["top"] - row[0]["top"] > 14:
                break
            detail_positions.extend(_center(word) for word in following if _label(word, visual) in _DETAILS)
        centers = {field: _center(word) for field, word in columns.items()}
        if detail_positions:
            centers["details"] = sum(detail_positions) / len(detail_positions)
        fields = sorted(centers, key=centers.get)
        rtl_names = visual and centers["location"] < centers.get("details", 0) < centers["serial"]
        boundaries = []
        for left, right in zip(fields, fields[1:]):
            boundary = (centers[left] + centers[right]) / 2
            if rtl_names and (left, right) == ("qty", "name"):
                boundary = min(boundary, columns["qty"]["x1"] + 4)
            if rtl_names and (left, right) == ("name", "location"):
                boundary = max(boundary, columns["location"]["x0"] - 8)
            boundaries.append(boundary)
        return _Header(index, row[0]["top"], fields, boundaries, visual, rtl_names)
    return None


def _cells(row, header: _Header) -> dict[str, str]:
    buckets = {field: [] for field in header.fields}
    for word in row:
        field = header.fields[bisect_right(header.boundaries, _center(word))]
        buckets[field].append(word)
    return {field: _rtl_name(words) if field == "name" and header.rtl_names else _text(words, header.visual)
            for field, words in buckets.items() if words}


def _is_anchor(cells: dict[str, str]) -> bool:
    serial = parse_int(cells.get("serial"))
    if serial is None or serial <= 0:
        return False
    if cells.get("name"):
        return True
    numbers = (parse_int(cells.get("qty")), parse_decimal(cells.get("price")),
               parse_percent(cells.get("discount")), parse_decimal(cells.get("total")))
    return sum(value is not None for value in numbers) >= 2


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
    split: float | None = None
    size: tuple[float, float] | None = None
    lines: list[dict[str, str]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    header_texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_page(self, page) -> bool:
        words = page.extract_words(x_tolerance=1.5)
        page_rows = _rows(words)
        size = (page.width, page.height)
        left, _, right, _ = page.bbox
        split = _copy_split(page_rows)
        if split is None:
            split = self.split if self.split is not None and self.size == size else (left + right) / 2
        lanes = [_rows([word for word in words if (_center(word) >= split) == side]) for side in (0, 1)]
        headers = [_find_header(rows) for rows in lanes]
        candidates = [side for side, header in enumerate(headers) if header is not None]
        if len(candidates) == 2 or any(headers[side].has_details for side in candidates):
            self.side = max(candidates, key=lambda side: (headers[side].has_details, side == self.side, side))
            self.split = split
            rows, header = lanes[self.side], headers[self.side]
        elif self.header and self.size == size and self.side is not None:
            rows, header = lanes[self.side], headers[self.side]
        else:
            rows, header = page_rows, _find_header(page_rows)
            if header:
                self.side = self.split = None
        if header:
            self.header = header
            self.size = size
        elif not self.header or self.size != size:
            return False
        footer_top = self._footer_top(lanes if self.side is not None else [rows], after=self.header.top)
        self._read_page(rows, start=header.index + 1 if header else 0, stop=footer_top)
        return True

    def _footer_top(self, lanes, *, after: float) -> float:
        stop = float("inf")
        for rows in lanes:
            for row in rows:
                text = _text(row, self.header.visual)
                top = row[0]["top"]
                if top > after and _NET_LABEL.search(text):
                    stop = min(stop, top)
                    self._read_metadata(text, before_table=False)
                elif normalize_text(text) == "شهري":
                    self.metadata.setdefault("payment_method", "credit")
        return stop

    def _read_page(self, rows, *, start: int, stop: float) -> None:
        header = self.header
        table = []
        anchors = []
        anchor_tops = []
        for index, row in enumerate(rows):
            top = row[0]["top"]
            if index < start:
                text = _text(row, header.visual)
                self._read_metadata(text, before_table=True)
                if "invoice_date" not in self.metadata:
                    for word in row:
                        if date := parse_date(word["text"]):
                            self.metadata["invoice_date"] = date
                            break
            elif top < stop:
                if len(row) == 1 and _label(row[0], header.visual) == "شهري":
                    continue
                cells = _cells(row, header)
                table.append((top, cells, 3 * max(word["height"] for word in row)))
                if _is_anchor(cells):
                    line = {key: value for key, value in cells.items() if key in _GRID_COLUMNS}
                    anchors.append(line)
                    anchor_tops.append(top)
                elif cells.get("serial") and any(key in cells for key in _AMOUNT_FIELDS):
                    self.warnings.append("unparsed:pdf_item")

        for top, cells, reach in table:
            next_anchor = bisect_right(anchor_tops, top)
            if next_anchor and "serial" not in cells:
                previous = next_anchor - 1
                if top - anchor_tops[previous] <= reach:
                    # Missing fields may be on the next baseline of this numbered item.
                    line = anchors[previous]
                    if name := cells.get("name"):
                        line["name"] = " ".join(filter(None, (line.get("name"), name)))
                    for key in _AMOUNT_FIELDS:
                        if key in cells:
                            line.setdefault(key, cells[key])
            if details := cells.get("details"):
                nearby = range(max(0, next_anchor - 1), min(len(anchors), next_anchor + 1))
                if nearby:
                    nearest = min(nearby, key=lambda index: abs(anchor_tops[index] - top))
                    if abs(anchor_tops[nearest] - top) <= reach:
                        _read_details(details, anchors[nearest])
        self.lines.extend(anchors)

    def _read_metadata(self, text: str, *, before_table: bool) -> None:
        for key, patterns in _METADATA.items():
            if match := next((match for pattern in patterns if (match := pattern.search(text))), None):
                raw = match[1]
                value = {"invoice_date": parse_date, "declared_total": parse_decimal}.get(key, str)(raw)
                if value is not None:
                    self.metadata.setdefault(key, value)
        if before_table:
            if "شهري" in normalize_text(text).split():
                self.metadata.setdefault("payment_method", "credit")
            self.header_texts.append(text)
            if re.match(r"^(?:مخزن|شركة|شركه|مستودع)\s+\S", text):
                self.metadata.setdefault("supplier_name", text)
            if re.fullmatch(_META_VALUES["invoice_date"], text) and (date := parse_date(text)):
                self.metadata.setdefault("invoice_date", date)

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
        if self.warnings or len(result.lines) != len(self.lines):
            result.warnings.append("unparsed:pdf_item")
        return result
