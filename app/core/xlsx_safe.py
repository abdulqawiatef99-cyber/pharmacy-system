"""Spreadsheet formula injection guard (#139).

openpyxl stores any string that starts with "=" as a live formula, and Excel treats text typed or
pasted with = + - @ (or a leading tab / carriage return) as the start of one. Item and supplier names
can come from supplier or E-Stock files, so every exported workbook passes through ``as_text`` right
before it is saved: each such string cell is written as plain text, never as a formula.
"""
from __future__ import annotations

TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def as_text(wb) -> None:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and (cell.data_type == "f" or v.startswith(TRIGGERS)):
                    cell.data_type = "s"
