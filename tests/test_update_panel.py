"""The update panel offers only the signed release path."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
ESC = re.search(r"\nconst esc = .*?\}\[c\]\)\);\n", JS, re.S).group(0)
FNS = "".join(re.search(rf"\nfunction {name}\(.*?\n\}}\n", JS, re.S).group(0)
              for name in ("updatePanelHtml", "smartLastHtml", "smartUpdateHtml"))

HARNESS = ESC + FNS + r"""
const failed = { result: { state: "rolled_back" }, enabled: true, available: false };
const out = [
  updatePanelHtml({ available: true, smart: failed }),
  updatePanelHtml({ available: false, smart: failed }),
  updatePanelHtml({ available: true, smart: { result: {}, enabled: true, available: true, notes: [] } }),
];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestUpdatePanel(unittest.TestCase):
    def test_last_result_never_exposes_an_unsigned_update(self):
        r = subprocess.run([shutil.which("node"), "-e", HARNESS], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        classic, current, smart = json.loads(r.stdout)
        self.assertIn("رجع لوحده", classic)
        self.assertNotIn('id="updGo"', classic)
        self.assertIn("رجع لوحده", current)
        self.assertIn("البرنامج محدّث", current)
        self.assertIn('id="updNow"', smart)
        self.assertNotIn('id="updGo"', smart)                    # the smart path wins when it is available


if __name__ == "__main__":
    unittest.main()
