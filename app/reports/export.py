"""Any table on screen -> a real Excel file (right-to-left, header row, money as numbers).
The screen sends what it already shows; nothing new is read from the database, so an export
can never reveal more than the person is allowed to see."""
from __future__ import annotations

import io
import re
from datetime import datetime

from ..core.errors import ValidationFailed

TYPES = ("text", "money", "int", "num", "date")
MAX_ROWS = 50_000
MAX_SHEETS = 8


def _text(cell, v) -> None:
    """Always plain text: a name like "=HYPERLINK(...)" must never become an Excel formula."""
    cell.value = str(v)[:500]
    cell.data_type = "s"


def _sheet_name(n: str, used: set[str]) -> str:
    n = re.sub(r"[\[\]:*?/\\]", " ", str(n or "Sheet")).strip()[:28] or "Sheet"
    base, i = n, 2
    while n in used:
        n, i = f"{base} {i}", i + 1
    used.add(n)
    return n


def xlsx(spec: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    sheets = spec.get("sheets")
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= MAX_SHEETS:
        raise ValidationFailed("لا يوجد جدول للتصدير")
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for sh in sheets:
        cols, rows = sh.get("columns") or [], sh.get("rows") or []
        if not cols or len(rows) > MAX_ROWS:
            raise ValidationFailed("الجدول فارغ أو كبير جدًا")
        cols = [{"label": str(c.get("label", ""))[:80], "type": c.get("type") if c.get("type") in TYPES else "text"} for c in cols]
        ws = wb.create_sheet(_sheet_name(sh.get("name"), used))
        ws.sheet_view.rightToLeft = True
        r0 = 1
        for line in (spec.get("title"), spec.get("subtitle")):
            if line:
                c0 = ws.cell(r0, 1)
                _text(c0, str(line)[:200])
                c0.font = Font(bold=r0 == 1, size=13 if r0 == 1 else 10)
                r0 += 1
        if r0 > 1:
            r0 += 1
        for j, c in enumerate(cols, 1):
            cell = ws.cell(r0, j)
            _text(cell, c["label"])
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E8E6DF")
            cell.alignment = Alignment(horizontal="center")
        widths = [len(c["label"]) + 2 for c in cols]
        for i, row in enumerate(rows, r0 + 1):
            if not isinstance(row, list):
                raise ValidationFailed("صف غير صحيح")
            for j, c in enumerate(cols, 1):
                v = row[j - 1] if j - 1 < len(row) else None
                if v in (None, ""):
                    continue
                cell = ws.cell(i, j)
                t = c["type"]
                try:
                    if t == "money":
                        cell.value, cell.number_format = round(int(v) / 100, 2), "#,##0.00"
                    elif t == "int":
                        cell.value, cell.number_format = int(v), "#,##0"
                    elif t == "num":
                        cell.value = float(v)
                    elif t == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
                        cell.value, cell.number_format = datetime.strptime(str(v), "%Y-%m-%d").date(), "yyyy-mm-dd"
                    else:
                        _text(cell, v)
                except (TypeError, ValueError):
                    _text(cell, v)
                widths[j - 1] = max(widths[j - 1], min(60, len(str(v)) + 2))
        if sh.get("total"):
            tr = r0 + len(rows) + 1
            for j, v in enumerate(sh["total"], 1):
                if v in (None, ""):
                    continue
                cell = ws.cell(tr, j)
                if cols[j - 1]["type"] == "money" and isinstance(v, int):
                    cell.value, cell.number_format = round(v / 100, 2), "#,##0.00"
                elif isinstance(v, (int, float)):
                    cell.value = v
                else:
                    _text(cell, v)
                cell.font = Font(bold=True)
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(r0 + 1, 1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
