"""Repair Arabic text extracted from visually-ordered PDFs.

The supplier PDFs (Orga Soft / Crystal Reports print-to-PDF) store Arabic glyphs in
visual (left-to-right) order, so pdfplumber returns each name reversed. The fix:
reverse the string, expand presentation forms (NFKC), then restore the original
left-to-right order of digit runs and Latin runs, and mirror brackets.
"""
import re
import unicodedata

_LTR_RUN = re.compile(r"[0-9٠-٩۰-۹]+(?:[.,/٫٬\-x*%][0-9٠-٩۰-۹]+)*"
                      r"|[A-Za-z][A-Za-z0-9.\-_/+&]*")
_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def fix_visual_arabic(s: str) -> str:
    """Convert a visually-ordered extracted string to logical order."""
    if not s:
        return s
    r = s[::-1]
    r = unicodedata.normalize("NFKC", r)
    r = _LTR_RUN.sub(lambda m: m.group(0)[::-1], r)
    r = r.translate(_MIRROR)
    return re.sub(r"\s+", " ", r).strip()


def has_arabic(s: str) -> bool:
    return bool(re.search(r"[؀-ۿﭐ-﷿ﹰ-﻿]", s or ""))
