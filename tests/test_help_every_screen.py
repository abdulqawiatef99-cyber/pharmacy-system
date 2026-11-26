"""Update 1 (owner 21/11): «اشرح الشاشة دي» on every screen of the program, one mechanism (route -> short
explanation, or the screen's guided tour), with English. Fails when a route of app.js has no help text."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
EN = (WEB / "i18n-en.js").read_text(encoding="utf-8")
NO_HELP = {"login", "setup"}             # before anyone is signed in


def sample_hashes():
    """One hash per route of app.js, the groups filled with a sample value."""
    block = JS[JS.index("\nconst routes = ["):]
    block = block[:block.index("\n];")]
    out = []
    for rx in re.findall(r"^\s*\[/\^(#\\/.*?)\$/,", block, re.M):
        h = rx.replace("\\/", "/")
        h = re.sub(r"\(\?:\\\?\.\*\)\?", "", h)                    # an optional ?query
        h = re.sub(r"\(\?:/\((?:[^()]|\([^()]*\))*\)\)\?", "", h)    # an optional /segment
        h = re.sub(r"\((?:\?:)?([a-z_|]+)\)", lambda m: m.group(1).split("|")[0], h)   # (a|b) -> a
        h = re.sub(r"\([^()]*\)", "x1", h)                          # any other group
        out.append(h)
    return out


HELP_JS = re.search(r"\nconst HELP = \{.*?\n\};\n", JS, re.S).group(0)
KEY_JS = re.search(r"\nconst helpKey = .*?\n", JS).group(0)


@unittest.skipUnless(shutil.which("node"), "Node not available")
class EveryRouteHasHelp(unittest.TestCase):
    def test_no_route_without_text(self):
        hashes = [h for h in sample_hashes() if h.split("/")[1].split("?")[0] not in NO_HELP]
        self.assertGreater(len(hashes), 40)
        run = "let location = { hash: '' };" + HELP_JS + KEY_JS + \
            f"console.log(JSON.stringify({json.dumps(hashes)}.map((h) => {{ location.hash = h; const k = helpKey();" \
            " return [h, k, k ? HELP[k][0] : null]; })))"
        r = subprocess.run([shutil.which("node"), "-e", run], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual([h for h, k, _ in rows if not k], [])
        for h, k, line in rows:
            self.assertIn(json.dumps(line, ensure_ascii=False) + ":", EN, f"no English for the help of {h}")

    def test_the_button_uses_the_tour_or_the_help(self):
        self.assertIn("const k = tourScreen() || helpKey();", JS)
        self.assertIn("if (!(TOUR[k] && tourStart(k))) showHelp();", JS)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, list] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  for (const h of JSON.parse(list)) {
    await p.evaluate(() => { document.querySelector('#app').innerHTML = ''; });   // nothing left from the last screen
    await p.goto(base + '/' + h);
    await p.waitForFunction((x) => location.hash === x, h);
    try { await p.waitForSelector('#app [data-tour-replay]', { timeout: 4000 }); out[h] = true; } catch { out[h] = false; }
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class ButtonOnEveryScreenInBrowser(APICase):
    def test_every_list_screen_shows_it(self):
        from app.sales.cash import CashDrawer
        CashDrawer(self.db).open(self.ctx, 0)
        hashes = [h for h in sample_hashes() if "x1" not in h and h.split("/")[1] not in NO_HELP]
        out = run_node(self, SCRIPT, "owner", OWNER_PW, json.dumps(hashes))
        self.assertEqual([h for h, ok in out.items() if not ok], [])


if __name__ == "__main__":
    unittest.main()
