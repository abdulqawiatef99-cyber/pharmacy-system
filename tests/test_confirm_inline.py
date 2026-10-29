"""Trial #5 (#43): the two-click confirmation in app.js (`confirmInline`) runs in Node with fake timers.
Skipped only where Node is missing (CI runners have it)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
FN = re.search(r"\nfunction confirmInline\(.*?\n\}\n", JS, re.S).group(0)

HARNESS = r"""
let now = 0, timers = [];
global.setTimeout = (f, ms) => { timers.push({ at: now + ms, f }); return timers.length; };
global.clearTimeout = (id) => { if (id) timers[id - 1].f = null; };
const tick = (ms) => { now += ms; for (const t of timers) if (t.f && t.at <= now) { const f = t.f; t.f = null; f(); } };
""" + FN + r"""
const btn = { dataset: {}, textContent: "اعتمد" }, out = [];
out.push(confirmInline(btn));            // 1st click: arms
tick(6000);                               // a careful person reads for 6 s
out.push(confirmInline(btn));            // 2nd click still confirms
out.push(btn.textContent);                // label restored at once
out.push(confirmInline(btn));            // 3rd quick click: a new first click, not a second action
tick(10001);
out.push(btn.textContent, !!btn.dataset.armed);
out.push(confirmInline(btn)); tick(3000); out.push(confirmInline(btn)); tick(9000);   // old timer never resets a new arming
out.push(confirmInline(btn), btn.textContent);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestConfirmInline(unittest.TestCase):
    def test_window_disarm_and_single_timer(self):
        r = subprocess.run([shutil.which("node"), "-e", HARNESS], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [False, True, "اعتمد", False, "اعتمد", False,
                                                False, True, False, "متأكد؟ اضغط مرة أخرى"])


if __name__ == "__main__":
    unittest.main()
