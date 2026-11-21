"""U2-15: every screen is right-to-left in Arabic and left-to-right in English (a real browser, every list route)."""
import re
from pathlib import Path

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
ROUTES = sorted(set(re.findall(r"\[/\^#\\/([a-z-]+)\$/", JS)) - {"login", "setup"})

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, routes] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = { ar: {}, en: {} };
  for (const lang of ['ar', 'en']) {
    await p.evaluate(async (l) => fetch('/api/me/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json',
      'X-Requested-With': 'pharm' }, body: JSON.stringify({ lang: l }) }), lang);
    await p.evaluate((l) => { try { localStorage.setItem('prefs', JSON.stringify({ lang: l })); } catch (e) {} }, lang);
    for (const r of routes.split(',')) {
      await p.goto(base + '/#/' + r);
      await p.reload();
      await p.waitForFunction((l) => document.documentElement.lang === l, lang, { timeout: 10000 }).catch(() => {});
      out[lang][r] = await p.evaluate(() => document.documentElement.dir);
    }
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestDirection(APICase):
    def test_every_screen_both_languages(self):
        self.assertGreater(len(ROUTES), 30)
        out = run_node(self, SCRIPT, "owner", OWNER_PW, ",".join(ROUTES))
        self.assertEqual({r for r, d in out["ar"].items() if d != "rtl"}, set())
        self.assertEqual({r for r, d in out["en"].items() if d != "ltr"}, set())
