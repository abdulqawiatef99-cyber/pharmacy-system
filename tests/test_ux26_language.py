"""UX-26 PR2 (owner 26/11): English letters in capitals (typed and shown, stored data untouched), and the keyboard
follows the field: an Arabic field asks for the Arabic layout, an English field (barcode, English name, codes) for
the English one. The call is fire-and-forget and debounced; a missing or refusing endpoint is ignored."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app" / "web" / "app.js").read_text(encoding="utf-8")


def _fn(name):
    m = re.search(rf"\nfunction {name}\(.*?\n\}}\n", JS, re.S)
    return m.group(0) if m else ""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestLanguageHelpers(unittest.TestCase):
    def node(self, code):
        r = subprocess.run([shutil.which("node"), "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_ascii_upper_only_touches_english_letters(self):
        out = self.node(_fn("upperEn") + r"""
console.log(JSON.stringify([upperEn("panadol extra 500mg"), upperEn("بانادول"), upperEn("vit d 5µg"),
  upperEn(""), upperEn(null), upperEn("Mix بانادول abc")]));""")
        self.assertEqual(out, ["PANADOL EXTRA 500MG", "بانادول", "VIT D 5µG", "", "", "MIX بانادول ABC"])

    def test_keyboard_switch_is_debounced_deduplicated_and_quiet(self):
        out = self.node(_fn("keyboardSwitcher") + r"""
const calls = []; let mode = "ok";
const fakeFetch = (url, opts) => { calls.push([url, JSON.parse(opts.body).lang]);
  return mode === "404" ? Promise.resolve({ status: 404, ok: false }) : mode === "net" ? Promise.reject(new Error("x"))
    : Promise.resolve({ status: 200, ok: true }); };
const sw = keyboardSwitcher(fakeFetch, 30);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  sw("ar"); sw("en"); sw("ar");          // fast focus changes: one call, the last one
  await wait(60);
  sw("ar"); await wait(60);              // same language again: no call
  sw("en"); await wait(60);
  mode = "net"; sw("ar"); await wait(60); // a network error is swallowed
  sw(null); await wait(60);              // an unmarked field: no call
  mode = "404"; sw("en"); await wait(60); // 404: the switcher turns itself off
  sw("ar"); await wait(60); sw("en"); await wait(60);
  console.log(JSON.stringify(calls));
})();""")
        self.assertEqual(out, [["/api/system/keyboard-layout", "ar"], ["/api/system/keyboard-layout", "en"],
                               ["/api/system/keyboard-layout", "ar"], ["/api/system/keyboard-layout", "en"]])


class TestFieldsMarked(unittest.TestCase):
    def test_main_fields_carry_their_language(self):
        for snippet in ('<input name="name_en" lang="en" data-upper', '<input name="name_ar" lang="ar"',
                        '<input id="bcNew" lang="en"', '<input id="posQ" lang="en"', '<input name="code" lang="en"'):
            self.assertIn(snippet, JS, snippet)
        self.assertIn('document.addEventListener("focusin"', JS)

    def test_english_name_is_shown_in_capitals(self):
        self.assertRegex(JS, r"const itemText = .*upperEn\(")


if __name__ == "__main__":
    unittest.main()
