"""Money POSTs whose unanswered save, sent again, must replay instead of posting twice: they are in ONCE_PATHS
(app/web/app.js), so api() keeps their Idempotency-Key. Part 3 adds the exchange, the opening correction and
the supplier return (Codex P1 on #396)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestOncePaths(unittest.TestCase):
    def test_part_3_money_posts_keep_their_key(self):
        src = re.search(r"\nconst ONCE_PATHS = \[.*?\];\n", JS, re.S).group(0)
        paths = ["/api/sales/abc/exchange", "/api/opening-balances/supplier/abc", "/api/opening-balances/customer/abc",
                 "/api/supplier-returns", "/api/reconcile", "/api/sales/abc/lines"]
        r = subprocess.run([shutil.which("node"), "-e", src + f"console.log(JSON.stringify({json.dumps(paths)}.map((p) => ONCE_PATHS.some((re) => re.test(p)))))"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [True, True, True, True, True, False])
