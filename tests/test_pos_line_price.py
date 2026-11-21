"""Launch day 21/11: the sale line shows the price of the unit it is sold in. 1 box of a 3-strip item showed
32.00 (the strip) while the total was the box's 96.00."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
FN = re.search(r"\nfunction linePrice\(.*?\n\}\n", JS, re.S).group(0)
RUN = "const money = (p) => (p == null ? '' : (p / 100).toFixed(2));\n" + FN + r"""
const it3 = { pack_size: 3 }, it1 = { pack_size: 1 };
const a = (unit, pack) => ({ unit_price: unit, pack_price: pack });
console.log(JSON.stringify([
  linePrice({ qty_base: 3, item: it3, allocations: [a(3200, 9600)] }),          // 1 box
  linePrice({ qty_base: 6, item: it3, allocations: [a(3200, 9600)] }),          // 2 boxes
  linePrice({ qty_base: 1, item: it3, allocations: [a(3200, 9600)] }),          // 1 strip
  linePrice({ qty_base: 4, item: it3, allocations: [a(3200, 9600)] }),          // 1 box + 1 strip
  linePrice({ qty_base: 2, item: it1, allocations: [a(1500, 1500)] }),          // a single-unit item
  linePrice({ qty_base: 3, item: it3, allocations: [a(3200, 9600), a(3400, 10200)] }),  // two batches, two prices
  linePrice({ qty_base: 3, item: it3, allocations: [a(3334, null)] }),          // no pack price stored
]));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestLinePrice(unittest.TestCase):
    def test_price_of_the_sold_unit(self):
        r = subprocess.run([shutil.which("node"), "-e", RUN], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["96.00", "96.00", "32.00", "96.00 / 32.00", "15.00",
                                                "96.00 ، 102.00", "100.02"])

    def test_the_cart_uses_it(self):
        self.assertIn('<td class="num">${linePrice(l)}</td>', JS)
        self.assertNotIn("l.allocations.map((a) => money(a.unit_price))", JS)


if __name__ == "__main__":
    unittest.main()
