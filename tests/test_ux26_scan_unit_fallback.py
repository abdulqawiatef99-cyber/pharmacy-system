"""UX-26 (owner's live test 26/11): scanning an item whose stock has no full pack left but still has strips/units
adds one unit instead of refusing, with a toast; with nothing left the add goes through as a pack and the server
still refuses it (never sell beyond stock)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def _fn(name):
    m = re.search(rf"\nfunction {name}\(.*?\n\}}\n", JS, re.S)
    return m.group(0) if m else ""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestScanUnitFallback(unittest.TestCase):
    def run_js(self, body):
        r = subprocess.run([shutil.which("node"), "-e", _fn("scanAddQty") + body], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_rule(self):
        out = self.run_js(r"""
const it = (o) => ({ pack_size: 3, available_qty: 9, is_service: false, ...o });
console.log(JSON.stringify([
  scanAddQty(it({}), false),                        // a full box on the shelf -> a box
  scanAddQty(it({ available_qty: 3 }), false),      // exactly one box -> a box
  scanAddQty(it({ available_qty: 2 }), false),      // two strips only -> one strip, flagged
  scanAddQty(it({ available_qty: 0 }), false),      // nothing -> a box: the server refuses as today
  scanAddQty(it({ available_qty: 2 }), true),       // the cashier asked for a unit -> a unit, no toast
  scanAddQty(it({ pack_size: 1, available_qty: 0 }), false),
  scanAddQty(it({ is_service: true, available_qty: null }), false),
  scanAddQty(it({ available_qty: null }), false),   // unknown stock -> a box
]));
""")
        self.assertEqual(out, [
            [1, 0, False], [1, 0, False], [0, 1, True], [1, 0, False],
            [0, 1, False], [1, 0, False], [1, 0, False], [1, 0, False]])

    def test_the_auto_add_uses_it_and_tells_the_cashier(self):
        self.assertIn("scanAddQty(res[0], unit)", JS)
        self.assertIn("مفيش علبة كاملة، اتنزل", JS)


if __name__ == "__main__":
    unittest.main()
