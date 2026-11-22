"""Update 3 new screens open in the browser with no script error (signed in as the owner, English and Arabic)."""
import json

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, routes] = process.argv.slice(1);
""" + LOGIN + r"""
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  const out = {};
  for (const [hash, sel] of JSON.parse(routes)) {
    await p.goto(base + '/' + hash);
    try { await p.waitForSelector(sel, { timeout: 8000 }); out[hash] = true; } catch { out[hash] = false; }
  }
  out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestUpdate3Screens(APICase):
    ROUTES = [["#/system", "select[name=rounding]"]]

    def test_screens_open(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, json.dumps(self.routes()))
        self.assertEqual(out.pop("errors"), [])
        self.assertTrue(all(out.values()), out)

    def routes(self):
        return self.ROUTES
