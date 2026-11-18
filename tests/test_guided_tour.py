"""F36 (#247): first-run guided tour. A bubble with an arrow walks the top bar on a person's first sign-in,
then each main screen explains itself once; «اشرح الشاشة دي» replays a screen; Esc closes; «تخطّي» stops the
automatic parts. "Seen" is kept per pharmacy + user in the browser (blocked storage: for this tab only).
It never blocks a sale: on the sale screen the focus stays in the search box and a scan still adds the item.
Synthetic data only."""
import re
import shutil
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.inventory import service as inv
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")


class TourSource(unittest.TestCase):
    def test_one_marked_section_no_library_no_new_shell_file(self):
        self.assertEqual(JS.count("F36 guided tour (#247) — BEGIN"), 1)
        self.assertEqual(JS.count("F36 guided tour — END"), 1)
        sec = JS[JS.index("F36 guided tour (#247) — BEGIN"):JS.index("F36 guided tour — END")]
        self.assertNotIn("import ", sec)
        self.assertNotIn("tour.js", (WEB / "index.html").read_text(encoding="utf-8"))
        # every storage access is guarded: a blocked browser storage never breaks the screen
        self.assertEqual(sec.count("localStorage."), len(re.findall(r"try \{[^\n]*localStorage\.", sec)))

    def test_every_part_covers_the_asked_controls(self):
        for part in ("main", "pos", "purchases", "items", "shift", "reports", "system"):
            self.assertRegex(JS, rf"\n  {part}: \[")
        for sel in ("#posQ", "#posRes [data-add]", ".paym", "#posDone", "#posKick", "a[data-nav=shift]",
                    "a[data-nav=reports]", "a[data-nav=system]", "#bkBox", "#upForm"):
            self.assertIn(sel, JS)
        for word in ("التالي", "السابق", "تخطّي", "اشرح الشاشة دي"):
            self.assertIn(word, JS)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, code] = process.argv.slice(1);
""" + LOGIN + r"""
  const out = {};
  const bub = () => p.locator('#tourBub');
  const title = async () => (await bub().count()) ? (await p.locator('#tourT').innerText()) : null;
  await p.setViewportSize({ width: 1366, height: 768 });
  await p.goto(base + '/#/home');
  await p.waitForSelector('#tourBub', { timeout: 10000 });
  out.first = await title();
  out.side = await bub().getAttribute('data-side');
  out.prevOff = await p.locator('[data-tour-go="-1"]').isDisabled();
  await p.locator('[data-tour-go="1"]').click();
  out.second = await title();
  await p.locator('[data-tour-go="-1"]').click();
  out.back = await title();
  // the ring never takes the mouse
  out.ringPointer = await p.$eval('#tourRing', (e) => getComputedStyle(e).pointerEvents);
  for (let i = 0; i < 30 && (await bub().count()); i++) await p.locator('[data-tour-go="1"]').click();
  out.closedAtEnd = (await bub().count()) === 0;
  out.store = await p.evaluate(() => Object.fromEntries(Object.keys(localStorage).filter((k) => k.startsWith('tour:')).map((k) => [k, JSON.parse(localStorage[k])])));
  await p.reload(); await p.waitForSelector('#topbar:not([hidden])'); await p.waitForTimeout(800);
  out.noRepeat = (await bub().count()) === 0;

  // sale screen: its part opens once, the focus stays in the search box and a scan still adds the item
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#tourBub', { timeout: 10000 });
  out.posFirst = await title();
  await p.locator('#posQ').focus();
  await p.keyboard.type(code, { delay: 5 }); await p.keyboard.press('Enter');
  await p.waitForSelector('table.cart [data-minus]', { timeout: 10000 });
  out.posLines = await p.locator('table.cart [data-minus]').count();
  out.tourStillOpen = (await bub().count()) === 1;
  await p.locator('[data-tour-go="1"]').click();
  out.posFocus = await p.evaluate(() => document.activeElement.id);
  await p.keyboard.press('Escape');
  out.escClosed = (await bub().count()) === 0;
  out.posSeen = await p.evaluate(() => Object.values(localStorage).some((v) => v.includes('"pos":1')));
  await p.locator('[data-tour-replay="pos"]').click();
  out.replay = await title();

  // dark mode + a narrow window: the bubble uses the theme and stays inside the window
  await p.keyboard.press('Escape');
  await p.evaluate(async () => fetch('/api/me/prefs', { method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ theme: 'dark' }) }));
  await p.setViewportSize({ width: 380, height: 700 });
  await p.goto(base + '/#/purchases'); await p.reload();
  await p.waitForSelector('#tourBub', { timeout: 10000 });
  out.purFirst = await title();
  out.darkBg = await p.$eval('#tourBub', (e) => getComputedStyle(e).backgroundColor);
  out.box = await p.$eval('#tourBub', (e) => { const r = e.getBoundingClientRect(); return [r.left, r.right, innerWidth, document.documentElement.scrollWidth]; });
  out.dir = await p.$eval('#tourBub', (e) => getComputedStyle(e).direction);

  // «تخطّي»: no more automatic parts for this person
  await p.locator('[data-tour-go="skip"]').click();
  await p.goto(base + '/#/items'); await p.waitForSelector('#q'); await p.waitForTimeout(800);
  out.skipStops = (await bub().count()) === 0;
  out.replayBtn = await p.locator('[data-tour-replay="items"]').count();

  // blocked browser storage: the tour still works and the screen does not break
  const p2 = await (await b.newContext()).newPage();
  await p2.goto(base + '/'); await p2.waitForSelector('#loginForm');
  await p2.addInitScript(() => { Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } }); });
  await p2.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p2.goto(base + '/#/home');
  await p2.waitForSelector('#tourBub', { timeout: 10000 });
  out.blockedFirst = await p2.locator('#tourT').innerText();
  await p2.keyboard.press('Escape');
  await p2.goto(base + '/#/items');
  await p2.waitForSelector('#tourBub', { timeout: 10000 });
  out.blockedNext = await p2.locator('#tourT').innerText();       // the top bar is not shown again in this tab
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TourInBrowser(APICase):
    def test_first_sign_in_tour_screens_replay_esc_skip_dark_narrow(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Tour Item", "public_price": 1000})
        cat.add_barcode(self.db, self.ctx, it, "6221043010359")
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        out = run_node(self, SCRIPT, "owner", OWNER_PW, "6221043010359")
        self.assertEqual(out["first"], "الصفحة الرئيسية")
        self.assertEqual(out["side"], "up")
        self.assertTrue(out["prevOff"])
        self.assertEqual(out["second"], "شاشة البيع")
        self.assertEqual(out["back"], "الصفحة الرئيسية")
        self.assertEqual(out["ringPointer"], "none")
        self.assertTrue(out["closedAtEnd"])
        self.assertEqual(list(out["store"].values()), [{"main": 1}])
        self.assertEqual(list(out["store"]), [f"tour:{self.ctx.org_id}:{self.ctx.user_id}"])   # per pharmacy + user
        self.assertTrue(out["noRepeat"])
        self.assertEqual(out["posFirst"], "البحث")
        self.assertEqual(out["posLines"], 1)
        self.assertTrue(out["tourStillOpen"])
        self.assertEqual(out["posFocus"], "posQ")
        self.assertTrue(out["escClosed"])
        self.assertTrue(out["posSeen"])
        self.assertEqual(out["replay"], "البحث")
        self.assertEqual(out["purFirst"], "قراءة فاتورة المورد")
        self.assertEqual(out["darkBg"], "rgb(36, 40, 44)")                               # --panel in dark mode
        left, right, width, scroll = out["box"]
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, width)
        self.assertLessEqual(scroll, width)
        self.assertEqual(out["dir"], "rtl")
        self.assertTrue(out["skipStops"])
        self.assertEqual(out["replayBtn"], 1)
        self.assertEqual(out["blockedFirst"], "الصفحة الرئيسية")
        self.assertEqual(out["blockedNext"], "البحث")


if __name__ == "__main__":
    unittest.main()
