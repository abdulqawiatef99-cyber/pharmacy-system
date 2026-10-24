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


def read_excel_sheets(data: bytes) -> list[tuple[str, list[list]]]:
    """All non-empty sheets, instruction sheets excluded, a sheet named 'data'
    first, then by number of filled rows."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # corrupt / password protected
        raise ValidationFailed("تعذر قراءة ملف Excel") from e
    out = []
    for ws in wb.worksheets:
        if ws.title.strip().lower() in INSTRUCTION_SHEETS:
            continue
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
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
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def read_pdf(data: bytes) -> tuple[list[list], list[str], bool]:
    """Returns (grid, page_texts, has_text). has_text False => scanned PDF."""
    import pdfplumber
    grid: list[list] = []
    texts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:20]:
                t = page.extract_text() or ""
                texts.extend(t.splitlines())
                for table in page.extract_tables():
                    grid.extend(table)
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
