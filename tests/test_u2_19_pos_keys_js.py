"""U2-19 on the screen: the sale screen reads its keys from «تخصيص الواجهة» (ME.ui.pos_keys) — a changed key acts,
the old one stops; nothing in the handler is still tied to a fixed F-key except the fixed ones (F8, F12)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.core import ui_settings

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestPosKeys(unittest.TestCase):
    def test_changed_key_maps_to_its_action(self):
        start = JS.index("const POS_KEY_DEFAULTS")
        end = JS.index("const POS_KEYS_ORDER")
        code = JS[start:end] + r"""
let ME = { ui: { pos_keys: { ...POS_KEY_DEFAULTS, done: "Ctrl+Enter", search: "F2", branch_stock: "F1" } } };
console.log(JSON.stringify([posAction("Ctrl+Enter"), posAction("F9"), posAction("F2"), posAction("F1"), POS_KEY_DEFAULTS]));
"""
        r = subprocess.run([shutil.which("node"), "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        done, old, f2, f1, defaults = json.loads(r.stdout)
        self.assertEqual((done, old, f2, f1), ("done", None, "search", "branch_stock"))
        self.assertEqual(defaults, ui_settings.POS_KEYS_DEFAULT)          # the screen and the server agree

    def test_handler_has_no_fixed_keys_left(self):
        h = JS[JS.index("  const act = posAction(k);"):JS.index("// Manual drawer opening (#81)")]
        self.assertEqual(re.findall(r'k === "(F\d+|Insert|Ctrl\+\w+)"', h), ["F8"])

    def test_choices_match_the_server_and_no_typing_keys(self):
        m = re.search(r"const POS_KEY_CHOICES = \[(.*?)\];", JS, re.S)
        choices = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(tuple(choices), ui_settings.POS_KEYS_ALLOWED)
        for k in ("Home", "End", "PageUp", "PageDown", "Ctrl+Insert", "F5", "F8", "F12", "Delete", "Escape"):
            self.assertNotIn(k, choices)

    def test_ctrl_enter_in_search_is_not_a_search(self):
        self.assertIn('if (e.key !== "Enter" || e.ctrlKey) return;', JS)
