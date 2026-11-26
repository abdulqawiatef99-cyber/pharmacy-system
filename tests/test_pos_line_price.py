"""Launch day 21/11: the sale line shows the price of the unit it is sold in. 1 box of a 3-strip item showed
32.00 (the strip) while the total was the box's 96.00."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
FN = re.search(r"\nfunction salePriceQty\(.*?\nasync function openDraft", JS, re.S).group(0).rsplit("\nasync", 1)[0]
RUN = r"""
const money = (p) => (p / 100).toFixed(2);
const esc = (s) => s;
const unitName = ({base_unit_name}) => base_unit_name || "وحدة";
""" + FN + r"""
const pack = (qty, price) => ({qty, price, pack: true});
const unit = (qty, price) => ({qty, price, pack: false});
const line = (...official_prices) => linePrice({item: {base_unit: "شريط"}, official_prices});
console.log(JSON.stringify([
  line(pack(1, 9600)),
  line(pack(2, 9600)),
  line(unit(1, 3200)),
  line(pack(1, 9600), unit(1, 3200)),
  line(unit(2, 1500)),
  line(pack(1, 9600), pack(1, 10200)),
  line(unit(3, 3334)),
]));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestLinePrice(unittest.TestCase):
    def test_price_of_the_sold_unit(self):
        r = subprocess.run([shutil.which("node"), "-e", RUN], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["96.00", "96.00", "32.00", "1 علبة × 96.00<br>1 شريط × 32.00",
                                                "15.00", "1 علبة × 96.00<br>1 علبة × 102.00", "33.34"])

    def test_the_cart_uses_it(self):
        self.assertIn('<td class="num">${linePrice(l)}</td>', JS)
        self.assertNotIn("l.allocations.map((a) => money(a.unit_price))", JS)


if __name__ == "__main__":
    unittest.main()
