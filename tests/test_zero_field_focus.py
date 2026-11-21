"""Launch day 21/11: a number field showing 0 / 0.00 is selected on focus on every screen, so typing replaces
the zero (opening the shift: typing 1185 in front of "0" gave 11850). A field with a real value is left alone."""
import re
import unittest
from pathlib import Path

from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestOnePlaceForEveryScreen(unittest.TestCase):
    def test_document_level_handler(self):
        self.assertIn('document.addEventListener("focusin"', JS)
        self.assertRegex(JS, r"Number\(el\.value\) === 0")
        self.assertEqual(len(re.findall(r"const isZeroField", JS)), 1)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/shift');
  const f = p.locator('#shOpen input[name=v]');
  await f.waitFor();
  const out = { before: await f.inputValue() };
  await f.click();                                   // a mouse click, like the cashier
  await p.keyboard.type('1185');
  out.clicked = await f.inputValue();
  await f.fill('0.00');
  await p.locator('body').click({ position: { x: 5, y: 5 } });
  await f.focus();                                   // keyboard focus (Tab)
  await p.keyboard.type('20');
  out.tabbed = await f.inputValue();
  await f.fill('35');
  await p.locator('body').click({ position: { x: 5, y: 5 } });
  await f.click();
  await p.keyboard.press('End');
  await p.keyboard.type('0');
  out.real = await f.inputValue();                   // a real value is not wiped
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestZeroFieldInBrowser(APICase):
    def test_typing_replaces_the_zero(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertEqual(float(out["before"]), 0.0)
        self.assertEqual(out["clicked"], "1185")
        self.assertEqual(out["tabbed"], "20")
        self.assertEqual(out["real"], "350")


if __name__ == "__main__":
    unittest.main()
