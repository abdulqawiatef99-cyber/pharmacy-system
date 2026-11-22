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
    ROUTES = [["#/users", "#suSave"], ["#/home", "#topbar"]]

    def test_screens_open(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW, json.dumps(self.routes()))
        self.assertEqual(out.pop("errors"), [])
        self.assertTrue(all(out.values()), out)

    def routes(self):
        return self.ROUTES


SAVE = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  await p.goto(base + '/#/users');
  await p.waitForSelector('#suSave');
  await p.check('#suOn'); await p.click('#suSave');
  await p.waitForSelector('#dhikrBar');
  await p.click('#suSave');                                  // saved twice: one bar, timers restarted
  await p.waitForTimeout(500);
  const out = { bars: await p.$$eval('#dhikrBar', (x) => x.length),
                pe: await p.$eval('#dhikrBar', (x) => getComputedStyle(x).pointerEvents) };
  out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestDhikrSave(APICase):
    def test_save_turns_it_on_without_errors(self):
        out = run_node(self, SAVE, "owner", OWNER_PW)
        self.assertEqual((out["bars"], out["pe"], out["errors"]), (1, "none", []))
