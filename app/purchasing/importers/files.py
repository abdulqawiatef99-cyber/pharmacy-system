"""File type detection + readers that produce cell grids for table.py."""
from __future__ import annotations

import csv
import io
import zipfile

from ...core.errors import ValidationFailed
from ..model import ExtractedInvoice
from .table import grid_to_invoice

KINDS = {"excel", "csv", "pdf", "image"}


def sniff(data: bytes, filename: str) -> tuple[str, str]:
    """Returns (kind, mime) from magic bytes, never from the filename alone."""
    name = (filename or "").lower()
    if data[:5] == b"%PDF-":
        return "pdf", "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "xl/workbook.xml" in z.namelist():
                    return "excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        except zipfile.BadZipFile:
            pass
        raise ValidationFailed("ملف مضغوط غير مدعوم")
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValidationFailed("صيغة Excel القديمة (.xls) غير مدعومة حاليًا؛ احفظ الملف بصيغة .xlsx",
                               code="xls_unsupported")
    if name.endswith((".csv", ".txt")) or _looks_text(data):
        return "csv", "text/csv"
    raise ValidationFailed("نوع الملف غير مدعوم (المدعوم: Excel .xlsx، CSV، PDF، صور JPG/PNG)",
                           code="unsupported_file")


def _looks_text(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    for enc in ("utf-8", "cp1256"):
        try:
            sample.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


INSTRUCTION_SHEETS = {"تعليمات", "instructions", "readme"}
# A 15 MB xlsx can unpack to gigabytes or declare millions of empty cells; any user who may upload
# a supplier file could stall the server for everyone (#140). Far above a real E-Stock export.
MAX_XLSX_UNPACKED = 256 * 1024 * 1024
MAX_XLSX_CELLS = 5_000_000
# A sheet without its declared size and with one cell far down makes openpyxl hand back an empty row for
# every missing one: a 5 KB file became a million rows that cost nothing of the cell budget (#324 X19).
MAX_XLSX_EMPTY_ROWS = 100_000
TOO_BIG = "ملف Excel كبير جدًا (عدد الخلايا أو حجمه بعد فك الضغط أكبر من المسموح)"


def check_xlsx_size(data: bytes) -> None:
    """Refuse a workbook whose parts unpack past MAX_XLSX_UNPACKED, before openpyxl opens it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile:
        return                                   # openpyxl refuses it with the usual message
    if total > MAX_XLSX_UNPACKED:
        raise ValidationFailed(TOO_BIG, code="too_large")


def sheet_rows(ws, budget: list[int]) -> list[list]:
    """All rows of a sheet; ``budget[0]`` is the cells left for the whole workbook. A row without cells
    costs one cell, and a sheet may hold MAX_XLSX_EMPTY_ROWS of them."""
    rows, empty = [], 0
    for r in ws.iter_rows(values_only=True):
        budget[0] -= len(r) or 1
        if not r:
            empty += 1
        if budget[0] < 0 or empty > MAX_XLSX_EMPTY_ROWS:
            raise ValidationFailed(TOO_BIG, code="too_large")
        rows.append(list(r))
    return rows


def read_excel_sheets(data: bytes) -> list[tuple[str, list[list]]]:
    """All non-empty sheets, instruction sheets excluded, a sheet named 'data'
    first, then by number of filled rows."""
    from openpyxl import load_workbook
    check_xlsx_size(data)
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # corrupt / password protected
        raise ValidationFailed("تعذر قراءة ملف Excel") from e
    out = []
    budget = [MAX_XLSX_CELLS]
    for ws in wb.worksheets:
        if ws.title.strip().lower() in INSTRUCTION_SHEETS:
            continue
        try:
            rows = sheet_rows(ws, budget)
        except ValidationFailed:
            wb.close()
            raise
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if rows:
            out.append((ws.title, rows))
    wb.close()
    out.sort(key=lambda t: (t[0].strip().lower() != "data", -len(t[1])))
    return out


def read_excel(data: bytes) -> list[list]:
    sheets = read_excel_sheets(data)
    return sheets[0][1] if sheets else []


def read_csv(data: bytes) -> list[list]:
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=CSV_DELIMITERS)
    except csv.Error:
        # The sniffer wants the same count on every line, so a title line above the table ("رقم الفاتورة: …")
        # made it give up, and a ';' or tab file (Excel's CSV on many Arabic Windows setups) came out as
        # one column per line. Fall back to the delimiter found on the most lines.
        dialect = type("Guessed", (csv.excel,), {"delimiter": _guess_delimiter(text[:4096])})
    return [row for row in csv.reader(io.StringIO(text), dialect)]


CSV_DELIMITERS = ",;\t|"


def _guess_delimiter(sample: str) -> str:
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    return max(CSV_DELIMITERS, key=lambda d: (sum(d in ln for ln in lines), sum(ln.count(d) for ln in lines),
                                              d == ","))


MAX_PDF_PAGES = 60     # far above a supplier invoice; a longer file is refused, never cut (#324 S23)


def read_pdf(data: bytes) -> tuple[list[list], list[str], bool]:
    """Returns (grid, page_texts, has_text). has_text False => scanned PDF."""
    import pdfplumber
    grid: list[list] = []
    texts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ValidationFailed(f"ملف PDF فيه {len(pdf.pages)} صفحة؛ الحد {MAX_PDF_PAGES}. قسّم الفاتورة",
                                       code="pdf_too_long")
            for page in pdf.pages:
                t = page.extract_text() or ""
                texts.extend(t.splitlines())
                for table in page.extract_tables():
                    grid.extend(table)
    except ValidationFailed:
        raise
    except Exception as e:
        raise ValidationFailed("تعذر قراءة ملف PDF") from e
    has_text = sum(len(t.strip()) for t in texts) > 40
    if has_text and not grid:
        # text PDF without ruled tables: split lines on runs of 2+ spaces
        import re
        grid = [re.split(r"\s{2,}", t.strip()) for t in texts if t.strip()]
    return grid, texts, has_text


def extract_structured(kind: str, data: bytes) -> ExtractedInvoice | None:
    """Excel/CSV/text-PDF extraction. Returns None for a scanned PDF (needs AI)."""
    if kind == "excel":
        return grid_to_invoice(read_excel(data), "structured")
    if kind == "csv":
        return grid_to_invoice(read_csv(data), "structured")
    if kind == "pdf":
        grid, texts, has_text = read_pdf(data)
        if not has_text:
            return None
        return grid_to_invoice(grid, "pdf_text", extra_text=texts)
    return None
