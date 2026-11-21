"""Trial #7 (#34): the browser receipt must stay inside the thermal printer's printable window.

An 80 mm head prints ~72 mm, a 58 mm head ~48 mm, and the driver decides where that window sits on
the paper. Centred content of width c on paper W survives a clip on either side only if c <= 2P - W.
"""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"


def mm(css: str, pattern: str) -> float:
    m = re.search(pattern, css)
    assert m, pattern
    return float(m.group(1))


class TestReceiptFitsPrintableWidth(unittest.TestCase):
    def test_80mm_content_fits_72mm_window(self):
        css = (WEB / "receipt.css").read_text(encoding="utf-8")
        page = mm(css, r"@page\s*\{\s*size:\s*([\d.]+)mm")
        width = mm(css, r"(?m)^\.r\s*\{[^}]*width:\s*([\d.]+)mm")
        self.assertEqual(page, 80)
        self.assertLessEqual(width, 2 * 72 - 80)
        self.assertRegex(css, r"(?m)^\.r\s*\{[^}]*margin:\s*0 auto")        # centred

    def test_58mm_content_fits_48mm_window(self):
        css = (WEB / "receipt.css").read_text(encoding="utf-8")
        css58 = (WEB / "receipt-58.css").read_text(encoding="utf-8")
        self.assertEqual(mm(css58, r"@page\s*\{\s*size:\s*([\d.]+)mm"), 58)
        self.assertLessEqual(mm(css, r"body\.w58 \.r\s*\{[^}]*width:\s*([\d.]+)mm"), 2 * 48 - 58)

    def test_page_side_margins_do_not_shift_content(self):
        for name in ("receipt.css", "receipt-58.css"):
            css = (WEB / name).read_text(encoding="utf-8")
            margin = re.search(r"@page\s*\{[^}]*margin:\s*([^;]+);", css).group(1).split()
            self.assertEqual(len(margin), 2, name)                          # vertical, horizontal
            self.assertIn(margin[1], ("0", "0mm"), name)

    def test_amount_column_has_fixed_width(self):
        css = (WEB / "receipt.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"table\s*\{[^}]*table-layout:\s*fixed")
        self.assertRegex(css, r"(?m)^td:last-child\s*\{[^}]*width:\s*[\d.]+mm")


if __name__ == "__main__":
    unittest.main()
