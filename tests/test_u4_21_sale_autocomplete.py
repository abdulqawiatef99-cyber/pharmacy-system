"""U4-21 on the sale screen: no field offers the browser's old typed values (a discount, an amount handed over,
another customer's address or phone). Every text or number box of the sale screen says autocomplete="off"."""
import re
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestSaleAutocompleteOff(unittest.TestCase):
    def test_every_sale_screen_box_is_off(self):
        start = JS.index("const proofInput = (s) =>")
        end = JS.index("const checkoutPrinted = new Set();")
        boxes = [t for t in re.findall(r"<input\b[^>]*>", JS[start:end])
                 if not re.search(r'type="(checkbox|radio|file|hidden)"', t)]
        self.assertGreater(len(boxes), 10)
        missing = [t for t in boxes if 'autocomplete="off"' not in t]
        self.assertEqual(missing, [])
